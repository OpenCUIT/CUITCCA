// ===== 对话持久化与回放：多会话版 =====
//
// 存储本体在 conversations.ts（每个会话独立消息列表）；本模块保留原来
// appendHistory/replayHistory 的函数名作为薄适配层，conversation.ts 的
// 读写路径不用感知"现在是哪个会话"。

import {
    Conversation,
    appendMessage,
    getActiveConversation,
    clearMessages as clearConvMessages,
} from './conversations';
import {
    appendBotBubble,
    appendUserBubble,
    applyLongAnswerCollapse,
    enhanceCodeBlocks,
    enhanceTables,
    renderMarkdown,
    scrollToBottom,
} from './dom';
import { decorateReplayedExchange } from './conversation';

export function activeConversation(): Conversation {
    return getActiveConversation();
}

export function appendHistory(role: string, content: string) {
    const conv = getActiveConversation();
    appendMessage(conv.id, role === 'user' ? 'user' : 'bot', content);
}

export function clearHistory() {
    // 只清当前会话（旧版是全局唯一历史，语义等价于"当前对话"）
    const conv = getActiveConversation();
    clearConvMessages(conv.id);
}

export function replayHistory() {
    const conv = getActiveConversation();
    const chatbox = document.getElementById('chatbox') as HTMLElement;
    if (conv.messages.length === 0) return;
    // 有历史记录时，移除默认欢迎语与首屏引导，改为回放真实历史
    chatbox.innerHTML = '';
    let lastUserEl: HTMLElement | null = null;
    let lastQuery = '';
    conv.messages.forEach((entry, idx) => {
        if (entry.role === 'user') {
            lastUserEl = appendUserBubble(entry.content).message;
            lastQuery = entry.content;
        } else {
            const { answerEl, message } = appendBotBubble();
            answerEl.innerHTML = renderMarkdown(entry.content);
            enhanceTables(answerEl);
            applyLongAnswerCollapse(answerEl);
            decorateReplayedExchange(
                lastUserEl, message, lastQuery, entry.content, idx === conv.messages.length - 1,
            );
        }
    });
    // 回放的历史不再变化，一次性做代码高亮
    enhanceCodeBlocks(chatbox);
    scrollToBottom(true);
}
