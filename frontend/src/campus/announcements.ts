// ===== 校园通知页面逻辑 (announcements.html) =====
// 数据源：GET /campus/announcements（列表/搜索/分类/分页）、
// GET /campus/announcements/{id}（详情）。列表项不带正文，点开详情时按需拉取。
// 依赖: sidebar.ts 已在上方加载；marked/DOMPurify 走 vendor 全局脚本。

import { apiFetch } from "../utils/api";

interface AnnouncementItem {
    id: number;
    title: string;
    category: string;
    source: string;
    source_url: string | null;
    published_at: string | null;
    summary: string;
    content?: string;
}

const PAGE_SIZE = 15;

// 弹层打开时暂存最后聚焦的触发按钮，关闭时归还焦点（键盘可用性）
let lastFocusedTrigger: HTMLElement | null = null;

function el<T extends HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) throw new Error(`缺少元素 #${id}`);
    return node as T;
}

function escapeHtml(text: string): string {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function renderMarkdown(raw: string): string {
    return DOMPurify.sanitize(marked.parse(raw || "", { breaks: true }));
}

const state = {
    query: "",
    category: "",
    page: 1,
    total: 0,
};

function totalPages(): number {
    return Math.max(1, Math.ceil(state.total / PAGE_SIZE));
}

function buildListUrl(): string {
    const params = new URLSearchParams();
    if (state.query) params.set("query", state.query);
    if (state.category) params.set("category", state.category);
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String((state.page - 1) * PAGE_SIZE));
    return `/campus/announcements?${params.toString()}`;
}

function renderChips(categories: { category: string; count: number }[]): void {
    const wrap = el<HTMLDivElement>("ann-categories");
    wrap.innerHTML = "";
    const all = document.createElement("button");
    all.type = "button";
    all.className = `campus_chip${state.category === "" ? " active" : ""}`;
    all.textContent = `全部 ${categories.reduce((acc, c) => acc + c.count, 0)}`;
    all.addEventListener("click", () => {
        state.category = "";
        state.page = 1;
        void loadCategories().then(loadList);
    });
    wrap.appendChild(all);
    for (const { category, count } of categories) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `campus_chip${state.category === category ? " active" : ""}`;
        chip.textContent = `${category} ${count}`;
        chip.addEventListener("click", () => {
            state.category = category;
            state.page = 1;
            void loadCategories().then(loadList);
        });
        wrap.appendChild(chip);
    }
}

async function loadCategories(): Promise<void> {
    try {
        const resp = await apiFetch("/campus/announcements/categories");
        if (resp.ok) renderChips(await resp.json());
    } catch {
        // 分类条加载失败不阻塞列表（假想场景：后端降级），静默保留旧分类
    }
}

function renderList(items: AnnouncementItem[]): void {
    const list = el<HTMLDivElement>("ann-list");
    list.innerHTML = "";
    for (const item of items) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "campus_item";
        card.innerHTML =
            `<div class="campus_item_title_row">` +
            `<span class="campus_tag">${escapeHtml(item.category)}</span>` +
            `<time class="campus_meta">${escapeHtml(item.published_at || "未知日期")}</time>` +
            `</div>` +
            `<div class="campus_item_title">${escapeHtml(item.title)}</div>` +
            `<div class="campus_item_summary">${escapeHtml(item.summary)}</div>` +
            `<div class="campus_meta">来源：${escapeHtml(item.source)}</div>`;
        card.addEventListener("click", () => void openDetail(item.id, card));
        list.appendChild(card);
    }
}

function renderPager(): void {
    el<HTMLButtonElement>("ann-prev").disabled = state.page <= 1;
    el<HTMLButtonElement>("ann-next").disabled = state.page >= totalPages();
    el("ann-page-info").textContent =
        state.total > 0 ? `第 ${state.page} / ${totalPages()} 页 · 共 ${state.total} 条` : "";
    el<HTMLDivElement>("ann-pager").style.display = state.total > 0 ? "" : "none";
}

async function loadList(): Promise<void> {
    const loading = el<HTMLDivElement>("ann-loading");
    const list = el<HTMLDivElement>("ann-list");
    const empty = el<HTMLDivElement>("ann-empty");
    loading.hidden = false;
    list.style.display = "none";
    empty.hidden = true;
    try {
        const resp = await apiFetch(buildListUrl());
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = (await resp.json()) as { total: number; items: AnnouncementItem[] };
        state.total = data.total;
        renderList(data.items);
        renderPager();
        empty.hidden = data.items.length > 0;
        list.style.display = data.items.length > 0 ? "" : "none";
    } catch (error) {
        console.error("加载通知失败:", error);
        empty.hidden = false;
        el<HTMLParagraphElement>("ann-empty").querySelector("p")!.textContent =
            "通知加载失败，请稍后刷新重试。";
        renderPager();
    } finally {
        loading.hidden = true;
    }
}

async function openDetail(id: number, trigger: HTMLElement): Promise<void> {
    lastFocusedTrigger = trigger;
    const overlay = el<HTMLDivElement>("ann-detail-overlay");
    const body = el<HTMLDivElement>("ann-detail-body");
    const title = el<HTMLHeadingElement>("ann-detail-title");
    title.textContent = "加载中…";
    body.innerHTML = `<div class="campus_meta">正在获取通知正文…</div>`;
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
    el<HTMLButtonElement>("ann-detail-close").focus();
    try {
        const resp = await apiFetch(`/campus/announcements/${id}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const item = (await resp.json()) as AnnouncementItem;
        title.textContent = item.title;
        el<HTMLSpanElement>("ann-detail-category").textContent = item.category;
        el<HTMLSpanElement>("ann-detail-date").textContent = `${item.published_at || ""} · ${item.source}`;
        body.innerHTML = renderMarkdown(item.content || item.summary || "（无正文）");
        const sourceEl = el<HTMLDivElement>("ann-detail-source");
        sourceEl.innerHTML = item.source_url
            ? `原文链接：<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.source_url)}</a>`
            : `来源：${escapeHtml(item.source)}`;
    } catch (error) {
        console.error("加载通知详情失败:", error);
        title.textContent = "加载失败";
        body.innerHTML = `<div class="campus_meta">通知正文获取失败，请关闭后重试。</div>`;
    }
}

function closeDetail(): void {
    el<HTMLDivElement>("ann-detail-overlay").hidden = true;
    document.body.style.overflow = "";
    lastFocusedTrigger?.focus();
    lastFocusedTrigger = null;
}

document.addEventListener("DOMContentLoaded", () => {
    const searchInput = el<HTMLInputElement>("ann-search");
    const applySearch = () => {
        state.query = searchInput.value.trim();
        state.page = 1;
        void loadList();
    };
    el<HTMLButtonElement>("ann-search-btn").addEventListener("click", applySearch);
    searchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") applySearch();
    });

    el<HTMLButtonElement>("ann-prev").addEventListener("click", () => {
        if (state.page > 1) {
            state.page -= 1;
            void loadList();
        }
    });
    el<HTMLButtonElement>("ann-next").addEventListener("click", () => {
        if (state.page < totalPages()) {
            state.page += 1;
            void loadList();
        }
    });

    el<HTMLButtonElement>("ann-detail-close").addEventListener("click", closeDetail);
    el<HTMLDivElement>("ann-detail-overlay").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) closeDetail();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !el<HTMLDivElement>("ann-detail-overlay").hidden) closeDetail();
    });

    void loadCategories();
    void loadList();
});
