// ===== 引用来源：行内角标 + 悬浮预览 + 可展开全文列表 =====
//
// 原来是答案下方一个「参考来源 (3)」折叠面板——想核对一句话的依据，要先跳出
// 阅读流、展开面板、再在几段片段里找。知识库类产品（Perplexity、各家带检索的
// 助手）通行做法是行内编号角标：答案末尾一排 [1][2][3]，悬浮直接看片段，点开
// 才展开完整列表。
//
// 诚实边界：这里的编号是**来源清单的序号**，不声称"第 2 句话出自 [2]"。要做到
// 句子级归因得让模型在生成时自己吐 [n] 标记，那是另一件事（要改 prompt、要防
// 模型编造不存在的编号、要单独评测），不能靠前端假装。

import { apiFetch } from '../utils/api';
import { getActiveConversation } from './conversations';

interface SourceNode {
    text: string;
    file_name?: string | null;
    score?: number | null;
}

function scoreText(node: SourceNode): string {
    return typeof node.score === 'number' && Number.isFinite(node.score)
        ? '相关度 ' + node.score.toFixed(3)
        : '';
}

function buildPreview(): HTMLElement {
    const preview = document.createElement('div');
    preview.className = 'citation_preview is-hidden';
    return preview;
}

function fillPreview(preview: HTMLElement, index: number, node: SourceNode) {
    preview.innerHTML = '';
    const head = document.createElement('div');
    head.className = 'citation_preview_head';
    head.textContent = [`[${index}] ` + (node.file_name || '未知来源'), scoreText(node)]
        .filter(Boolean)
        .join(' · ');
    const body = document.createElement('div');
    body.className = 'citation_preview_body';
    body.textContent = node.text.length > 260 ? node.text.slice(0, 260) + '…' : node.text;
    preview.appendChild(head);
    preview.appendChild(body);
}

function buildFullList(nodes: SourceNode[]): HTMLElement {
    const list = document.createElement('div');
    list.className = 'citations_list is-hidden';
    nodes.forEach((node, i) => {
        const item = document.createElement('div');
        item.className = 'citation_item';
        item.dataset.index = String(i + 1);

        const head = document.createElement('div');
        head.className = 'citation_head';
        // 头部：编号 + 来源文件名 + 相关度分数（重排后的 cross-encoder 分数，
        // 能直观看出"这条依据有多可信"；检索质量的透明是这个产品的核心看点）。
        head.textContent = [`[${i + 1}] ` + (node.file_name || '未知来源'), scoreText(node)]
            .filter(Boolean)
            .join(' · ');
        item.appendChild(head);

        const snippet = document.createElement('div');
        snippet.textContent = node.text.length > 200 ? node.text.slice(0, 200) + '…' : node.text;
        item.appendChild(snippet);

        // 复制按钮：复制的是**完整**原文，不是上面截断到 200 字的展示片段
        // ——核对信息时要的是全文。
        const copyBtn = document.createElement('button');
        copyBtn.className = 'citation_copy_btn';
        copyBtn.type = 'button';
        copyBtn.title = '复制';
        copyBtn.textContent = '📋';
        copyBtn.addEventListener('click', () => {
            navigator.clipboard?.writeText(node.text).then(() => {
                copyBtn.textContent = '✓';
                setTimeout(() => { copyBtn.textContent = '📋'; }, 1200);
            }).catch(() => { /* 静默 */ });
        });
        item.appendChild(copyBtn);
        list.appendChild(item);
    });
    return list;
}

export function renderCitations(citationsEl: HTMLElement, nodes: SourceNode[]) {
    citationsEl.innerHTML = '';

    const chips = document.createElement('div');
    chips.className = 'citation_chips';
    const label = document.createElement('span');
    label.className = 'citation_chips_label';
    label.textContent = '来源';
    chips.appendChild(label);

    const preview = buildPreview();
    const list = buildFullList(nodes);

    const showItem = (index: number) => {
        list.classList.remove('is-hidden');
        list.querySelectorAll('.citation_item').forEach((el) => el.classList.remove('highlight'));
        const target = list.querySelector(`.citation_item[data-index="${index}"]`);
        target?.classList.add('highlight');
        target?.scrollIntoView({ block: 'nearest' });
    };

    nodes.forEach((node, i) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'citation_chip';
        chip.textContent = String(i + 1);
        chip.title = node.file_name || '未知来源';
        chip.setAttribute('aria-label', `来源 ${i + 1}：${node.file_name || '未知来源'}`);

        const show = () => {
            fillPreview(preview, i + 1, node);
            preview.classList.remove('is-hidden');
            // 贴着角标定位：卡片挂在 .citations（position: relative）里，用
            // 角标相对父容器的偏移做左对齐，超出右边界时靠 CSS 的 max-width
            // 收住，不做复杂的碰撞检测。
            preview.style.left = Math.max(0, chip.offsetLeft - 8) + 'px';
        };
        const hide = () => preview.classList.add('is-hidden');
        chip.addEventListener('mouseenter', show);
        chip.addEventListener('focus', show);
        chip.addEventListener('mouseleave', hide);
        chip.addEventListener('blur', hide);
        chip.addEventListener('click', () => showItem(i + 1));
        chips.appendChild(chip);
    });

    const toggle = document.createElement('button');
    toggle.className = 'citations_toggle';
    toggle.type = 'button';
    toggle.textContent = `全部来源 (${nodes.length})`;
    toggle.addEventListener('click', () => list.classList.toggle('is-hidden'));
    chips.appendChild(toggle);

    citationsEl.appendChild(chips);
    citationsEl.appendChild(preview);
    citationsEl.appendChild(list);
    citationsEl.classList.remove('is-hidden');
}

export async function loadCitations(citationsEl: HTMLElement) {
    try {
        // conversation_id 必须带上：服务端按 {session}#{conversation} 存来源，
        // 不带的话多会话下永远取不到（见 router/graph_qa.query_sources）。
        const body = new URLSearchParams({ conversation_id: getActiveConversation().id });
        const response = await apiFetch('/graph/query_sources', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body,
        });
        if (!response.ok) return;
        const data = await response.json();
        const nodes = (data.source_nodes || []).filter((n: SourceNode) => n && n.text);
        if (nodes.length === 0) return;
        renderCitations(citationsEl, nodes);
    } catch (e) {
        // 引用来源是增强信息，静默失败不影响主对话
    }
}
