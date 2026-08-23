// ===== 会话栏：多会话的新建 / 切换 / 重命名 / 删除 =====
//
// 视图重建（回放消息或还原空状态）由 chat.ts 通过回调提供——本模块只管
// 会话数据与这根工具条，不直接操作 #chatbox，避免与 chat.ts 的空状态
// 快照机制耦合。切换会话不需要通知服务端：请求里的 conversation_id 已经
// 把服务端历史按会话隔离。

import {
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

function el<T extends HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) throw new Error(`缺少元素 #${id}`);
    return node as T;
}

function formatTime(ts: number): string {
    const diff = Date.now() - ts;
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} 天前`;
    return new Date(ts).toLocaleDateString('zh-CN');
}

function renderCurrentTitle() {
    const conv = getActiveConversation();
    el('session-current-title').textContent = conv.title || '新对话';
}

function closeMenu() {
    el('session-menu').classList.add('is-hidden');
    el('session-current').setAttribute('aria-expanded', 'false');
}

function renderMenu(callbacks: SessionsBarCallbacks) {
    const menu = el('session-menu');
    menu.innerHTML = '';
    const active = getActiveConversation();
    const conversations = listConversations();

    if (conversations.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'chat_session_empty';
        empty.textContent = '暂无其他会话';
        menu.appendChild(empty);
    }

    for (const conv of conversations) {
        const item = document.createElement('div');
        item.className = 'chat_session_item' + (conv.id === active.id ? ' active' : '');
        item.setAttribute('role', 'option');

        const main = document.createElement('button');
        main.type = 'button';
        main.className = 'chat_session_item_main';
        const title = document.createElement('span');
        title.className = 'chat_session_item_title';
        title.textContent = conv.title || '新对话';
        const time = document.createElement('span');
        time.className = 'chat_session_item_time';
        time.textContent = formatTime(conv.updatedAt);
        main.appendChild(title);
        main.appendChild(time);
        main.addEventListener('click', () => {
            if (conv.id !== getActiveConversation().id) {
                setActiveId(conv.id);
                renderCurrentTitle();
                callbacks.rebuildView();
            }
            closeMenu();
        });
        item.appendChild(main);

        const actions = document.createElement('div');
        actions.className = 'chat_session_item_actions';

        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'chat_session_icon_btn';
        renameBtn.title = '重命名';
        renameBtn.textContent = '✎';
        renameBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const next = window.prompt('重命名会话', conv.title);
            if (next !== null) {
                renameConversation(conv.id, next);
                renderCurrentTitle();
                renderMenu(callbacks);
            }
        });

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'chat_session_icon_btn chat_session_delete';
        deleteBtn.title = '删除会话';
        deleteBtn.textContent = '🗑';
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!window.confirm(`删除会话「${conv.title || '新对话'}」？该会话的本地记录将无法恢复。`)) {
                return;
            }
            const wasActive = conv.id === getActiveConversation().id;
            deleteConversation(conv.id);
            if (wasActive) {
                const rest = listConversations();
                if (rest.length > 0) {
                    setActiveId(rest[0].id);
                } else {
                    const fresh = createConversation();
                    addConversation(fresh);
                    setActiveId(fresh.id);
                }
                callbacks.rebuildView();
            }
            renderCurrentTitle();
            renderMenu(callbacks);
        });

        actions.appendChild(renameBtn);
        actions.appendChild(deleteBtn);
        item.appendChild(actions);
        menu.appendChild(item);
    }
}

export function initSessionsBar(callbacks: SessionsBarCallbacks): void {
    renderCurrentTitle();

    el('session-new').addEventListener('click', () => {
        const conv = createConversation();
        addConversation(conv);
        setActiveId(conv.id);
        renderCurrentTitle();
        callbacks.rebuildView();
        closeMenu();
    });

    el('session-current').addEventListener('click', () => {
        const menu = el('session-menu');
        const isOpen = !menu.classList.contains('is-hidden');
        if (isOpen) {
            closeMenu();
            return;
        }
        renderMenu(callbacks);
        menu.classList.remove('is-hidden');
        el('session-current').setAttribute('aria-expanded', 'true');
    });

    document.addEventListener('click', (e) => {
        const bar = document.getElementById('chat-sessions-bar');
        if (bar && !bar.contains(e.target as Node)) {
            closeMenu();
        }
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeMenu();
    });
}
