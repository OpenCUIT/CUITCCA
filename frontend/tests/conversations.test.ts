import { beforeEach, describe, expect, it } from 'vitest';
import {
  appendMessage,
  clearMessages,
  createConversation,
  deleteConversation,
  getActiveConversation,
  listConversations,
  popLastExchange,
  removeLastBotMessage,
  renameConversation,
  setActiveId,
} from '../src/chat/conversations';

const CONV_KEY = 'cuitcca_conversations_v1';
const LEGACY_KEY = 'cuitcca_chat_history_v1';

describe('conversations 多会话存储', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('首次访问自动创建活跃会话', () => {
    const conv = getActiveConversation();
    expect(conv.title).toBe('新对话');
    expect(conv.messages).toHaveLength(0);
    expect(getActiveConversation().id).toBe(conv.id);
  });

  it('首条用户消息成为标题；append 后能读回', () => {
    const conv = getActiveConversation();
    appendMessage(conv.id, 'user', '图书馆开馆时间是几点？');
    appendMessage(conv.id, 'bot', '工作日 8:00-22:00。');
    const reloaded = listConversations().find(c => c.id === conv.id)!;
    expect(reloaded.title).toBe('图书馆开馆时间是几点？');
    expect(reloaded.messages).toHaveLength(2);
    expect(reloaded.messages[1].content).toBe('工作日 8:00-22:00。');
  });

  it('popLastExchange 撤掉完整一轮（用户+回答）', () => {
    const conv = getActiveConversation();
    appendMessage(conv.id, 'user', 'q1');
    appendMessage(conv.id, 'bot', 'a1');
    appendMessage(conv.id, 'user', 'q2');
    appendMessage(conv.id, 'bot', 'a2');
    popLastExchange(conv.id);
    let reloaded = listConversations().find(c => c.id === conv.id)!;
    expect(reloaded.messages.map(m => m.content)).toEqual(['q1', 'a1']);
    popLastExchange(conv.id);
    reloaded = listConversations().find(c => c.id === conv.id)!;
    expect(reloaded.messages).toHaveLength(0);
  });

  it('removeLastBotMessage 只撤最后一条回答', () => {
    const conv = getActiveConversation();
    appendMessage(conv.id, 'user', 'q');
    appendMessage(conv.id, 'bot', 'a');
    removeLastBotMessage(conv.id);
    const reloaded = listConversations().find(c => c.id === conv.id)!;
    expect(reloaded.messages.map(m => m.role)).toEqual(['user']);
  });

  it('切换/删除/清空/重命名', () => {
    const c1 = getActiveConversation();
    appendMessage(c1.id, 'user', '第一个会话');
    const c2 = createConversation();
    c2.title = '手动新建';
    localStorage.setItem(CONV_KEY, JSON.stringify([...listConversations(), c2]));
    setActiveId(c2.id);
    expect(getActiveConversation().id).toBe(c2.id);

    renameConversation(c2.id, '改名后的会话');
    expect(listConversations().find(c => c.id === c2.id)!.title).toBe('改名后的会话');

    appendMessage(c2.id, 'user', 'x');
    clearMessages(c2.id);
    expect(getActiveConversation().messages).toHaveLength(0);

    deleteConversation(c2.id);
    expect(listConversations().find(c => c.id === c2.id)).toBeUndefined();
  });

  it('旧版单列表历史一次性迁移为会话', () => {
    localStorage.setItem(
      LEGACY_KEY,
      JSON.stringify([
        { role: 'user', content: '旧问题', ts: 1 },
        { role: 'bot', content: '旧回答', ts: 2 },
      ]),
    );
    const conv = getActiveConversation();
    expect(conv.title).toBe('旧问题');
    expect(conv.messages).toHaveLength(2);
    // 迁移幂等：不会重复导入
    const again = listConversations();
    expect(again).toHaveLength(1);
  });
});
