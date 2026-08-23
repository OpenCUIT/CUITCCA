// ===== 多会话存储：每个会话独立的消息列表 + 标题 + 时间，localStorage 持久化 =====
//
// 会话的服务端隔离靠请求里的 conversation_id（后端 graph_session.
// effective_client_id 把 cookie 会话键细分成 {session}#{conversation}），
// 这里只管浏览器侧的存储与切换 UI 需要的数据。

const CONVERSATIONS_KEY = 'cuitcca_conversations_v1';
const ACTIVE_KEY = 'cuitcca_active_conversation';
const LEGACY_HISTORY_KEY = 'cuitcca_chat_history_v1';
const MAX_CONVERSATIONS = 20;
const MAX_MESSAGES_PER_CONVERSATION = 50;

export interface StoredMessage {
    role: 'user' | 'bot';
    content: string;
    ts: number;
}

export interface Conversation {
    id: string;
    title: string;
    messages: StoredMessage[];
    updatedAt: number;
}

function readAll(): Conversation[] {
    try {
        const raw = localStorage.getItem(CONVERSATIONS_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

/** 会话数据变更事件：侧栏列表靠它刷新（标题在首条消息后才定下来）。 */
export const CONVERSATIONS_CHANGED_EVENT = 'cuitcca:conversations-changed';

function writeAll(conversations: Conversation[]): void {
    try {
        localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(conversations));
    } catch {
        // localStorage 不可用（隐私模式等）时静默跳过持久化
    }
    // 在唯一的写入出口广播，而不是让每个调用点自己记得刷新列表——漏一处就是
    // "发完消息侧栏标题还是「新对话」"这种只在特定路径下复现的 bug。
    if (typeof document !== 'undefined') {
        document.dispatchEvent(new CustomEvent(CONVERSATIONS_CHANGED_EVENT));
    }
}

export function newConversationId(): string {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
        return crypto.randomUUID();
    }
    return 'conv-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
}

export function createConversation(): Conversation {
    return { id: newConversationId(), title: '新对话', messages: [], updatedAt: Date.now() };
}

// 旧版单列表历史一次性迁移为第一个会话（只在多会话存储为空时做，幂等）
function migrateLegacyOnce(): void {
    if (readAll().length > 0) return;
    try {
        const raw = localStorage.getItem(LEGACY_HISTORY_KEY);
        if (!raw) return;
        const legacy = JSON.parse(raw) as Array<{ role: string; content: string; ts?: number }>;
        if (!Array.isArray(legacy) || legacy.length === 0) return;
        const firstUser = legacy.find(m => m.role === 'user');
        const conv = createConversation();
        conv.messages = legacy
            .filter(m => m.role === 'user' || m.role === 'bot')
            .map(m => ({ role: m.role as 'user' | 'bot', content: m.content, ts: m.ts || Date.now() }));
        if (firstUser) conv.title = firstUser.content.slice(0, 18);
        writeAll([conv]);
    } catch {
        // 迁移失败不影响使用：相当于从空会话开始
    }
}

export function listConversations(): Conversation[] {
    migrateLegacyOnce();
    return readAll().sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getActiveId(): string {
    const active = localStorage.getItem(ACTIVE_KEY);
    if (active && readAll().some(c => c.id === active)) return active;
    return '';
}

export function getActiveConversation(): Conversation {
    // 迁移要在这里也触发一次：首次访问的入口可能是任何读函数，
    // 不能依赖调用方先碰过 listConversations。
    migrateLegacyOnce();
    const all = readAll();
    const active = getActiveId();
    const found = all.find(c => c.id === active);
    if (found) return found;
    // 没有活跃会话（首次访问/被删光）：取最近一个，没有就建一个
    const latest = all.sort((a, b) => b.updatedAt - a.updatedAt)[0];
    if (latest) {
        localStorage.setItem(ACTIVE_KEY, latest.id);
        return latest;
    }
    const fresh = createConversation();
    writeAll([fresh]);
    localStorage.setItem(ACTIVE_KEY, fresh.id);
    return fresh;
}

export function setActiveId(id: string): void {
    localStorage.setItem(ACTIVE_KEY, id);
}

export function appendMessage(conversationId: string, role: 'user' | 'bot', content: string): void {
    const all = readAll();
    const conv = all.find(c => c.id === conversationId);
    if (!conv) return;
    conv.messages.push({ role, content, ts: Date.now() });
    if (conv.messages.length > MAX_MESSAGES_PER_CONVERSATION) {
        conv.messages = conv.messages.slice(-MAX_MESSAGES_PER_CONVERSATION);
    }
    // 首条用户消息作为会话标题（列表里可辨识）
    if (role === 'user' && (conv.title === '新对话' || !conv.title)) {
        conv.title = content.slice(0, 18);
    }
    conv.updatedAt = Date.now();
    writeAll(all);
}

export function popLastExchange(conversationId: string): void {
    const all = readAll();
    const conv = all.find(c => c.id === conversationId);
    if (!conv || conv.messages.length === 0) return;
    // 撤掉最后一条；如果它前面是用户消息（完整一轮），把用户那条也撤掉——
    // "重新生成"会把用户气泡重新发一遍，不撤会出现两条一样的提问。
    if (conv.messages[conv.messages.length - 1].role === 'bot' && conv.messages.length >= 2) {
        conv.messages = conv.messages.slice(0, -2);
    } else {
        conv.messages = conv.messages.slice(0, -1);
    }
    conv.updatedAt = Date.now();
    writeAll(all);
}

export function removeLastBotMessage(conversationId: string): void {
    const all = readAll();
    const conv = all.find(c => c.id === conversationId);
    if (!conv || conv.messages.length === 0) return;
    if (conv.messages[conv.messages.length - 1].role === 'bot') {
        conv.messages = conv.messages.slice(0, -1);
        conv.updatedAt = Date.now();
        writeAll(all);
    }
}

export function clearMessages(conversationId: string): void {
    const all = readAll();
    const conv = all.find(c => c.id === conversationId);
    if (!conv) return;
    conv.messages = [];
    conv.title = '新对话';
    conv.updatedAt = Date.now();
    writeAll(all);
}

export function renameConversation(conversationId: string, title: string): void {
    const all = readAll();
    const conv = all.find(c => c.id === conversationId);
    if (!conv) return;
    conv.title = title.trim().slice(0, 30) || '未命名对话';
    writeAll(all);
}

export function deleteConversation(conversationId: string): void {
    writeAll(readAll().filter(c => c.id !== conversationId));
    if (getActiveId() === conversationId) {
        localStorage.removeItem(ACTIVE_KEY);
    }
}

export function addConversation(conversation: Conversation): void {
    const all = readAll();
    all.push(conversation);
    // 超上限删最旧的（当前活跃的除外）
    const sorted = all.sort((a, b) => b.updatedAt - a.updatedAt);
    const active = getActiveId();
    let kept = 0;
    const result: Conversation[] = [];
    for (const c of sorted) {
        if (c.id === active || kept < MAX_CONVERSATIONS) {
            if (c.id !== active) kept += 1;
            result.push(c);
        }
    }
    writeAll(result);
    if (!result.some(c => c.id === active)) {
        localStorage.removeItem(ACTIVE_KEY);
    }
}
