// ===== 文档级管理面板（§02 第 4 个 tab）：按 doc_id 聚合的文档列表 =====
//
// 数据源 GET /index/{name}/documents（后端 list_documents 按 ref_doc_id 聚合
// Chroma chunk）。分块浏览器是 chunk 视图；这里回答"这个索引里有哪些文档、
// 各多大、要不要删/重建"——文件上传入库的文档还支持"重新索引"（后端从
// SAVE_PATH 的永久副本重新走摄取管道，用于分块规则变化后重建）。

import { showToast } from "../utils/toast";
import { apiFetch } from "../utils/api";
import { loadIndexNodes } from "./nodesPanel";
import { manageState } from "./state";

interface DocRow {
    doc_id: string;
    file_name: string;
    source_url: string;
    chunk_count: number;
    total_chars: number;
}

function el<T extends HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) throw new Error(`缺少元素 #${id}`);
    return node as T;
}

function displayName(doc: DocRow): string {
    return doc.file_name || doc.source_url || `文本 · ${doc.doc_id.slice(0, 10)}`;
}

function formatChars(n: number): string {
    if (n >= 10000) return (n / 10000).toFixed(1) + ' 万字';
    return n + ' 字';
}

export function resetDocumentsPanelForIndexSwitch() {
    el('doc-list').innerHTML = '<div class="doc_empty_hint">选择或载入索引以查看文档列表</div>';
    el('doc-count').textContent = '';
}

export async function loadIndexDocuments() {
    const indexName = manageState.currentActiveIndex;
    const listEl = el('doc-list');
    if (!indexName) {
        resetDocumentsPanelForIndexSwitch();
        return;
    }
    listEl.innerHTML = '<div class="doc_empty_hint">正在加载文档列表…</div>';
    try {
        const resp = await apiFetch(`/index/${encodeURIComponent(indexName)}/documents`);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = (await resp.json()) as { total: number; documents: DocRow[] };
        el('doc-count').textContent = `共 ${data.total} 份文档`;
        if (data.documents.length === 0) {
            listEl.innerHTML = '<div class="doc_empty_hint">该索引暂无文档，请先在"文件上传"导入</div>';
            return;
        }
        render(listEl, data.documents, indexName);
    } catch (e) {
        listEl.innerHTML = '<div class="doc_empty_hint">文档列表加载失败，请稍后重试</div>';
        console.error('加载文档列表失败:', e);
    }
}

function render(listEl: HTMLElement, docs: DocRow[], indexName: string) {
    listEl.innerHTML = '';
    for (const doc of docs) {
        const row = document.createElement('div');
        row.className = 'doc_row';

        const main = document.createElement('div');
        main.className = 'doc_row_main';
        const name = document.createElement('div');
        name.className = 'doc_row_name';
        name.textContent = displayName(doc);
        name.title = doc.source_url || doc.doc_id;
        const meta = document.createElement('div');
        meta.className = 'doc_row_meta';
        meta.textContent = `${doc.chunk_count} 个分块 · 约 ${formatChars(doc.total_chars)}`;
        main.appendChild(name);
        main.appendChild(meta);
        // 点击文档名 → 跳到分块浏览器并按 doc_id 过滤该文档全部分块
        main.addEventListener('click', () => {
            const nodeSearch = document.getElementById('node-search') as HTMLInputElement | null;
            if (nodeSearch) {
                nodeSearch.value = doc.doc_id;
                nodeSearch.dispatchEvent(new Event('input'));
            }
            document.getElementById('panel-right-container')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        row.appendChild(main);

        const actions = document.createElement('div');
        actions.className = 'doc_row_actions';

        // 重新索引：只对有源文件副本的文档有意义（file_name 存在且非"文本录入-"前缀）
        const isUploadedFile = !!doc.file_name && !doc.file_name.startsWith('文本录入-');
        if (isUploadedFile) {
            const reindexBtn = document.createElement('button');
            reindexBtn.type = 'button';
            reindexBtn.className = 'doc_action_btn';
            reindexBtn.textContent = '重新索引';
            reindexBtn.title = '删除旧分块后从源文件重新摄取（分块规则变化/内容更新后用）';
            reindexBtn.addEventListener('click', () => reindexDoc(doc, reindexBtn, indexName));
            actions.appendChild(reindexBtn);
        }

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'doc_action_btn doc_action_danger';
        deleteBtn.textContent = '删除';
        deleteBtn.addEventListener('click', async () => {
            if (!window.confirm(`删除文档「${displayName(doc)}」？它的 ${doc.chunk_count} 个分块都会一并删除。`)) {
                return;
            }
            deleteBtn.disabled = true;
            try {
                const resp = await apiFetch(
                    `/index/${encodeURIComponent(indexName)}/deleteDoc?doc_id=${encodeURIComponent(doc.doc_id)}`,
                    { method: 'POST' },
                );
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                showToast('文档已删除', 'success');
                await loadIndexDocuments();
                await loadIndexNodes(manageState.currentActiveIndex!);
            } catch (e) {
                deleteBtn.disabled = false;
                showToast('删除失败: ' + (e instanceof Error ? e.message : String(e)), 'error');
            }
        });
        actions.appendChild(deleteBtn);

        row.appendChild(actions);
        listEl.appendChild(row);
    }
}

async function reindexDoc(doc: DocRow, btn: HTMLButtonElement, indexName: string) {
    if (!window.confirm(`重新索引「${doc.file_name}」？旧分块会被删除并从源文件重建。`)) {
        return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = '重建中…';
    try {
        const resp = await apiFetch(`/index/${encodeURIComponent(indexName)}/reindex`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'file_name=' + encodeURIComponent(doc.file_name),
        });
        if (!resp.ok) {
            const detail = await resp.json().catch(() => null);
            throw new Error((detail && detail.message) || 'HTTP ' + resp.status);
        }
        showToast('重新索引完成', 'success');
        await loadIndexDocuments();
        await loadIndexNodes(manageState.currentActiveIndex!);
    } catch (e) {
        btn.disabled = false;
        btn.textContent = original;
        showToast('重新索引失败: ' + (e instanceof Error ? e.message : String(e)), 'error');
    }
}
