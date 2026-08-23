// ===== 聊天 DOM 基元：Markdown 渲染、气泡构建、滚动、加载态 =====
// 纯 DOM 构建，不做持久化——写不写 localStorage 由调用方（conversation/
// history）决定，避免本模块反向依赖 history 形成循环。

export function renderMarkdown(rawText: string): string {
    const html = marked.parse(rawText || '', { breaks: true });
    return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
}

// ===== 自动跟随滚动 =====
// 流式回答期间每帧都会调 scrollToBottom。用户想往上翻看前几轮时，无条件跟随
// 等于每来一个 token 就把人拽回底部，根本没法回读——所以一旦用户主动上滚就
// 脱离跟随，滚回底部附近再自动恢复，脱离期间显示"回到最新"按钮。
const NEAR_BOTTOM_PX = 80;
let autoFollow = true;

function scroller(): HTMLElement | null {
    // 真正装消息、可滚动的容器是 .talk_content（style.css 里 overflow-y:
    // auto 的那个），不是 .chat_bottom——.chat_bottom 是它的兄弟节点，待在
    // .talk_outline 这个外层容器里，可滚动余量恒为 0，scrollIntoView 在它
    // 身上是空操作（实测 12 轮对话后 scrollTop 停在 0，可滚动余量 1209px）。
    return document.querySelector('.talk_content') as HTMLElement | null;
}

function isNearBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
}

/** 滚到底部。``force`` 用于"用户明确要求回到最新"，忽略脱离状态。 */
export function scrollToBottom(force = false) {
    const el = scroller();
    if (!el) return;
    if (force) autoFollow = true;
    if (!autoFollow) return;
    el.scrollTop = el.scrollHeight;
}

export function initScrollFollow() {
    const el = scroller();
    const btn = document.getElementById('scroll-bottom-btn');
    if (!el) return;

    const sync = () => {
        autoFollow = isNearBottom(el);
        btn?.classList.toggle('is-hidden', autoFollow);
    };
    el.addEventListener('scroll', sync, { passive: true });
    btn?.addEventListener('click', () => {
        scrollToBottom(true);
        sync();
    });
    sync();
}

export function appendUserBubble(text: string): { message: HTMLElement; content: HTMLElement } {
    const chatbox = document.getElementById('chatbox') as HTMLElement;
    const message = document.createElement('div');
    message.className = 'message user';
    const content = document.createElement('div');
    content.className = 'content_man';
    content.textContent = text;
    const img = document.createElement('div');
    img.className = 'content_man_img';
    message.appendChild(content);
    message.appendChild(img);
    chatbox.appendChild(message);
    return { message, content };
}

export function appendBotBubble(): { message: HTMLElement; content: HTMLElement; answerEl: HTMLElement; citations: HTMLElement } {
    const chatbox = document.getElementById('chatbox') as HTMLElement;
    const message = document.createElement('div');
    message.className = 'message bot';
    const img = document.createElement('div');
    img.className = 'content_bot_img';
    const content = document.createElement('div');
    content.className = 'content_bot';
    const answerEl = document.createElement('div');
    answerEl.className = 'replycontent';
    content.appendChild(answerEl);
    const citations = document.createElement('div');
    citations.className = 'citations is-hidden';
    content.appendChild(citations);
    message.appendChild(img);
    message.appendChild(content);
    chatbox.appendChild(message);
    return { message, content, answerEl, citations };
}

export function showThinkingIndicator(answerEl: HTMLElement) {
    // 省略号由 .thinking-dots::after 的 CSS 动画逐段点亮，比静态"..."更有"正在
    // 打字"的临场感。
    answerEl.innerHTML = '<span class="thinking-indicator"><span class="thinking-spinner"></span>正在思考<span class="thinking-dots"></span></span>';
}

// ===== 代码高亮（highlight.js，vendor 全局脚本）=====
// 只在回答定格后调用一次（streaming 期间每个 rAF 都整段重绘 innerHTML，
// 逐帧跑 hljs 既贵又会因为半成品代码块闪跳）。每个 pre 加复制按钮。
export function enhanceCodeBlocks(scope: HTMLElement) {
    if (typeof hljs === 'undefined') return;
    scope.querySelectorAll('pre code').forEach(code => {
        const block = code as HTMLElement;
        try {
            // highlightElement 幂等标记：已高亮过的（历史回放重复调用）跳过
            if (!block.dataset.highlighted) {
                hljs.highlightElement(block);
            }
        } catch {
            // 未知语言等高亮失败：代码块原样保留，不影响正文
        }
        const pre = block.parentElement as HTMLElement | null;
        if (pre && !pre.querySelector('.code_copy_btn')) {
            pre.classList.add('has_copy_btn');
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'code_copy_btn';
            btn.textContent = '复制';
            btn.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(block.textContent || '');
                    btn.textContent = '✓ 已复制';
                    btn.classList.add('copied');
                    setTimeout(() => {
                        btn.textContent = '复制';
                        btn.classList.remove('copied');
                    }, 1500);
                } catch {
                    btn.textContent = '复制失败';
                }
            });
            pre.appendChild(btn);
        }
    });
}
