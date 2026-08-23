// ===== 会话列表（侧栏）：新建 / 搜索 / 切换 / 重命名 / 删除 =====
//
// 从"顶部一个下拉框"改成"侧栏一列会话"：会话是聊天产品的第一等对象，藏在
// 下拉里意味着用户要先点开才知道自己有哪些对话，也看不出哪个是当前的。
// 按更新时间分组（今天/昨天/前 7 天/更早）是主流 chat 的通行做法，比一列
// 「x 分钟前」更容易扫。
//
// 视图重建（回放消息或还原空状态）由 chat.ts 通过回调提供——本模块只管会话
// 数据与这块列表，不直接操作 #chatbox，避免与 chat.ts 的空状态快照机制耦合。
// 切换会话不需要通知服务端：请求里的 conversation_id 已经把服务端历史按会话
// 隔离。

import {
    CONVERSATIONS_CHANGED_EVENT,
    Conversation,
    addConversation,
    createConversation,
    deleteConversation,
    getActiveConversation,
    listConversations,
    renameConversation,
    setActiveId,
} from './conversations';

export interface SessionsBarCallbacks {
    /** 会话切换/新建/删除后重建消息区视图（回放或空状态） */
    rebuildView: () => void;
}

const DAY = 24 * 60 * 60 * 1000;

/** 按更新时间分组，返回 [组名, 会话[]][]（已按新到旧排序，空组不出现）。 */
export function groupConversations(
    conversations: Conversation[],
    now: number = Date.now(),
): [string, Conversation[]][] {
    const startOfToday = new Date(now).setHours(0, 0, 0, 0);
    const buckets: Record<string, Conversation[]> = { 今天: [], 昨天: [], '前 7 天': [], 更早: [] };
    for (const conv of [...conversations].sort((a, b) => b.updatedAt - a.updatedAt)) {
        const ts = conv.updatedAt;
        if (ts >= startOfToday) buckets['今天'].push(conv);
        else if (ts >= startOfToday - DAY) buckets['昨天'].push(conv);
        else if (ts >= startOfToday - 7 * DAY) buckets['前 7 天'].push(conv);
        else buckets['更早'].push(conv);
    }
    return Object.entries(buckets).filter(([, list]) => list.length > 0);
}

/** 搜索：标题 + 消息正文都匹配（只搜标题的话，"我上次问热水那次"根本找不到）。 */
export function filterConversations(conversations: Conversation[], keyword: string): Conversation[] {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return conversations;
    return conversations.filter((conv) => {
        if ((conv.title || '').toLowerCase().includes(kw)) return true;
        return conv.messages.some((m) => (m.content || '').toLowerCase().includes(kw));
    });
}

function iconButton(cls: string, label: string, glyph: string): HTMLButtonElement {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `conv_item_action ${cls}`;
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.textContent = glyph;
    return btn;
}

let searchKeyword = '';

function renderList(root: HTMLElement, callbacks: SessionsBarCallbacks) {
    const listEl = root.querySelector('#conv-list') as HTMLElement;
    if (!listEl) return;
    listEl.innerHTML = '';

    const activeId = getActiveConversation().id;
    const matched = filterConversations(listConversations(), searchKeyword);

    if (matched.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'conv_empty';
        empty.textContent = searchKeyword ? '没有匹配的对话' : '还没有对话';
        listEl.appendChild(empty);
        return;
    }

    for (const [groupName, conversations] of groupConversations(matched)) {
        const header = document.createElement('div');
        header.className = 'conv_group';
        header.textContent = groupName;
        listEl.appendChild(header);

        for (const conv of conversations) {
            const item = document.createElement('div');
            item.className = `conv_item${conv.id === activeId ? ' active' : ''}`;

            const main = document.createElement('button');
            main.type = 'button';
            main.className = 'conv_item_main';
            main.textContent = conv.title || '新对话';
            main.title = conv.title || '新对话';
            main.addEventListener('click', () => {
                if (conv.id === getActiveConversation().id) return;
                setActiveId(conv.id);
                callbacks.rebuildView();
                renderList(root, callbacks);
            });
            item.appendChild(main);

            const actions = document.createElement('div');
            actions.className = 'conv_item_actions';

            const renameBtn = iconButton('conv_rename', '重命名', '✎');
            renameBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const next = window.prompt('重命名会话', conv.title);
                if (next === null) return;
                renameConversation(conv.id, next.trim() || '新对话');
                renderList(root, callbacks);
            });
            actions.appendChild(renameBtn);

            const deleteBtn = iconButton('conv_delete', '删除', '🗑');
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!window.confirm(`删除会话「${conv.title || '新对话'}」？该会话的本地记录将无法恢复。`)) {
                    return;
                }
                const wasActive = conv.id === getActiveConversation().id;
                deleteConversation(conv.id);
                // 删掉当前会话后 getActiveConversation() 会自动落到最近一条或新建
                // 一条，视图必须跟着重建，否则消息区还停在已经不存在的会话上。
                if (wasActive) callbacks.rebuildView();
                renderList(root, callbacks);
            });
            actions.appendChild(deleteBtn);

            item.appendChild(actions);
            listEl.appendChild(item);
        }
    }
}

export function initSessionsBar(callbacks: SessionsBarCallbacks): void {
    const root = document.getElementById('side-conversations');
    if (!root) return; // 非聊天页（侧栏不渲染这块）

    root.innerHTML =
        `<button class="conv_new" id="conv-new" type="button">＋ 新对话</button>
         <div class="conv_search_wrap">
            <input class="conv_search" id="conv-search" type="search" placeholder="搜索对话" aria-label="搜索对话">
         </div>
         <div class="conv_list" id="conv-list" role="list"></div>`;

    (root.querySelector('#conv-new') as HTMLButtonElement).addEventListener('click', () => {
        const conv = createConversation();
        addConversation(conv);
        setActiveId(conv.id);
        callbacks.rebuildView();
        renderList(root, callbacks);
    });

    (root.querySelector('#conv-search') as HTMLInputElement).addEventListener('input', (e) => {
        searchKeyword = (e.target as HTMLInputElement).value;
        renderList(root, callbacks);
    });

    // 发消息会改标题和更新时间（首条用户消息即标题），列表要跟着变。
    // conversations.ts 在每次写盘时广播一个事件，比让 conversation.ts 到处
    // 记得手动刷新列表可靠。
    document.addEventListener(CONVERSATIONS_CHANGED_EVENT, () => renderList(root, callbacks));

    renderList(root, callbacks);
}
