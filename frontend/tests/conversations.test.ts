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

// ===== 侧栏会话列表：分组与搜索 =====
// 这两个纯函数决定"用户能不能找到自己上次那段对话"，是列表 UI 里唯一有逻辑
// 的部分（渲染部分靠 Playwright 覆盖）。

import { filterConversations, groupConversations } from '../src/chat/sessionsBar';

function conv(id: string, title: string, updatedAt: number, texts: string[] = []) {
    return {
        id,
        title,
        updatedAt,
        messages: texts.map((content, i) => ({ role: (i % 2 ? 'bot' : 'user') as 'user' | 'bot', content, ts: updatedAt })),
    };
}

describe('groupConversations', () => {
    const now = new Date('2026-08-23T15:00:00').getTime();
    const day = 86400000;

    it('按今天/昨天/前 7 天/更早分组，组内新到旧', () => {
        const groups = groupConversations([
            conv('a', '今天早些时候', new Date('2026-08-23T09:00:00').getTime()),
            conv('b', '刚刚', now - 60000),
            conv('c', '昨天', new Date('2026-08-22T20:00:00').getTime()),
            conv('d', '四天前', now - 4 * day),
            conv('e', '一个月前', now - 30 * day),
        ], now);

        expect(groups.map(([name]) => name)).toEqual(['今天', '昨天', '前 7 天', '更早']);
        expect(groups[0][1].map(c => c.id)).toEqual(['b', 'a']);
    });

    it('空组不出现', () => {
        const groups = groupConversations([conv('a', '刚刚', now - 1000)], now);
        expect(groups).toHaveLength(1);
        expect(groups[0][0]).toBe('今天');
    });
});

describe('filterConversations', () => {
    const list = [
        conv('a', '图书馆借阅', 1, ['本科生能借几本书']),
        conv('b', '公寓热水', 2, ['热水几点供应']),
    ];

    it('关键词为空时原样返回', () => {
        expect(filterConversations(list, '  ')).toHaveLength(2);
    });

    it('标题命中', () => {
        expect(filterConversations(list, '图书馆').map(c => c.id)).toEqual(['a']);
    });

    it('消息正文也参与匹配——只搜标题的话"我上次问热水那次"根本找不到', () => {
        expect(filterConversations(list, '几点供应').map(c => c.id)).toEqual(['b']);
    });
});
