// ===== 拖拽文件进对话：直接入知识库 =====
//
// 原来想让助手读一份新文件，得先离开对话去管理页、选索引、上传、再回来提问。
// 主流 chat 产品的做法是把文件直接拖进对话框。这里把两者接起来：拖进来的文件
// 走的仍然是管理页那条 `/index/{name}/uploadFiles` 摄取管道（分块 + sha256
// 去重 + UPSERTS），只是入口挪到了对话里。
//
// 有意不做的：不把文件内容塞进本轮 prompt。文件进的是知识库（可检索、可复用、
// 可在管理页看到），而不是"这一轮的附件"——后者需要一整套临时上下文机制，
// 而且用户下次提问就找不到它了。上传完成后在对话里回一条系统提示说明这一点。

import { apiFetch } from '../utils/api';
import { showToast } from '../utils/toast';

let cachedIndexName: string | null = null;

async function resolveIndexName(): Promise<string | null> {
    if (cachedIndexName) return cachedIndexName;
    try {
        const response = await apiFetch('/index/list');
        if (!response.ok) return null;
        const data = await response.json();
        const names: string[] = data.indexes || [];
        cachedIndexName = names[0] || null;
        return cachedIndexName;
    } catch {
        return null;
    }
}

function appendSystemNote(text: string, kind: 'info' | 'error' = 'info') {
    const chatbox = document.getElementById('chatbox');
    if (!chatbox) return;
    const note = document.createElement('div');
    note.className = `message system_note${kind === 'error' ? ' is-error' : ''}`;
    note.textContent = text;
    chatbox.appendChild(note);
    note.scrollIntoView({ block: 'nearest' });
}

async function uploadDroppedFiles(files: File[]) {
    const indexName = await resolveIndexName();
    if (!indexName) {
        appendSystemNote('还没有可用的知识库索引，无法接收文件。', 'error');
        return;
    }

    const names = files.map((f) => f.name).join('、');
    appendSystemNote(`正在把「${names}」加入知识库 ${indexName}…`);

    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    try {
        const response = await apiFetch(`/index/${encodeURIComponent(indexName)}/uploadFiles`, {
            method: 'POST',
            body: formData,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            // 后端对超限/格式不支持返回的是带 message 的 400，原样透出去比
            // "上传失败"有用得多（用户才知道是文件太大还是格式不支持）。
            appendSystemNote(data.message || '上传失败，请稍后再试。', 'error');
            return;
        }
        appendSystemNote(`已加入知识库：${names}。现在可以直接就这份内容提问了。`);
        showToast('文件已加入知识库', 'success');
    } catch {
        appendSystemNote('上传失败：网络错误。', 'error');
    }
}

export function initDropUpload() {
    const zone = document.querySelector('.talk_outline') as HTMLElement | null;
    if (!zone) return;

    const overlay = document.createElement('div');
    overlay.className = 'chat_drop_overlay is-hidden';
    overlay.innerHTML = '<div class="chat_drop_hint">松手即可加入知识库<br><span>支持 PDF / Word / Excel / PPT / Markdown / TXT 等</span></div>';
    zone.appendChild(overlay);

    // dragenter/dragleave 会在子元素之间来回冒泡，只用一个布尔量会闪。用计数器
    // 抵消进出，是拖放覆盖层的标准做法。
    let depth = 0;
    const show = () => overlay.classList.remove('is-hidden');
    const hide = () => { depth = 0; overlay.classList.add('is-hidden'); };

    zone.addEventListener('dragenter', (e) => {
        if (!e.dataTransfer?.types.includes('Files')) return;
        e.preventDefault();
        depth += 1;
        show();
    });
    zone.addEventListener('dragover', (e) => {
        if (!e.dataTransfer?.types.includes('Files')) return;
        e.preventDefault();
    });
    zone.addEventListener('dragleave', () => {
        depth -= 1;
        if (depth <= 0) hide();
    });
    zone.addEventListener('drop', (e) => {
        if (!e.dataTransfer?.files?.length) return;
        e.preventDefault();
        hide();
        void uploadDroppedFiles(Array.from(e.dataTransfer.files));
    });
}
