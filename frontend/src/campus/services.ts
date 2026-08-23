// ===== 校园服务页面逻辑 (services.html) =====
// 数据源：GET /campus/services（列表，数据量小不分页）、GET /campus/services/{id}
// （详情）。服务内容是完整 Markdown 办事指南，详情弹层里渲染。
// 依赖: sidebar.ts 已在上方加载；marked/DOMPurify 走 vendor 全局脚本。

import { apiFetch } from "../utils/api";

interface ServiceItem {
    id: number;
    name: string;
    category: string;
    icon: string;
    content: string;
    source: string;
}

let allServices: ServiceItem[] = [];
let activeCategory = "";

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

function renderChips(): void {
    const wrap = el<HTMLDivElement>("svc-categories");
    wrap.innerHTML = "";
    const counts = new Map<string, number>();
    for (const svc of allServices) counts.set(svc.category, (counts.get(svc.category) || 0) + 1);
    const all = document.createElement("button");
    all.type = "button";
    all.className = `campus_chip${activeCategory === "" ? " active" : ""}`;
    all.textContent = `全部 ${allServices.length}`;
    all.addEventListener("click", () => {
        activeCategory = "";
        renderChips();
        renderGrid();
    });
    wrap.appendChild(all);
    for (const [category, count] of counts) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `campus_chip${activeCategory === category ? " active" : ""}`;
        chip.textContent = `${category} ${count}`;
        chip.addEventListener("click", () => {
            activeCategory = category;
            renderChips();
            renderGrid();
        });
        wrap.appendChild(chip);
    }
}

function renderGrid(): void {
    const grid = el<HTMLDivElement>("svc-grid");
    const empty = el<HTMLDivElement>("svc-empty");
    grid.innerHTML = "";
    const items = allServices.filter((s) => !activeCategory || s.category === activeCategory);
    for (const svc of items) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "campus_svc_card";
        card.innerHTML =
            `<div class="campus_svc_icon">${escapeHtml(svc.icon || "📋")}</div>` +
            `<div class="campus_svc_name">${escapeHtml(svc.name)}</div>` +
            `<div class="campus_meta">${escapeHtml(svc.category)}</div>`;
        card.addEventListener("click", () => openDetail(svc, card));
        grid.appendChild(card);
    }
    empty.hidden = items.length > 0;
    grid.style.display = items.length > 0 ? "" : "none";
}

async function loadServices(): Promise<void> {
    const loading = el<HTMLDivElement>("svc-loading");
    loading.hidden = false;
    try {
        const resp = await apiFetch("/campus/services");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = (await resp.json()) as { items: ServiceItem[] };
        allServices = data.items;
        renderChips();
        renderGrid();
    } catch (error) {
        console.error("加载校园服务失败:", error);
        allServices = [];
        el<HTMLDivElement>("svc-empty").hidden = false;
        el<HTMLParagraphElement>("svc-empty").querySelector("p")!.textContent =
            "校园服务加载失败，请稍后刷新重试。";
    } finally {
        loading.hidden = true;
    }
}

function openDetail(svc: ServiceItem, trigger: HTMLElement): void {
    lastFocusedTrigger = trigger;
    el<HTMLHeadingElement>("svc-detail-title").textContent = `${svc.icon ? svc.icon + " " : ""}${svc.name}`;
    el<HTMLSpanElement>("svc-detail-category").textContent = svc.category;
    el<HTMLDivElement>("svc-detail-body").innerHTML = renderMarkdown(svc.content);
    el<HTMLDivElement>("svc-detail-source").textContent = `信息来源：${svc.source}`;
    el<HTMLDivElement>("svc-detail-overlay").hidden = false;
    document.body.style.overflow = "hidden";
    el<HTMLButtonElement>("svc-detail-close").focus();
}

function closeDetail(): void {
    el<HTMLDivElement>("svc-detail-overlay").hidden = true;
    document.body.style.overflow = "";
    lastFocusedTrigger?.focus();
    lastFocusedTrigger = null;
}

document.addEventListener("DOMContentLoaded", () => {
    const searchInput = el<HTMLInputElement>("svc-search");
    // 搜索走后端：后端会匹配 name/keywords/content（keywords 是种子数据里
    // 人工写的检索别名，"洗澡"能命中"学生公寓热水"），纯客户端按名称过滤做不到
    const applySearch = async () => {
        const keyword = searchInput.value.trim();
        activeCategory = "";
        try {
            const url = keyword
                ? `/campus/services?query=${encodeURIComponent(keyword)}`
                : "/campus/services";
            const resp = await apiFetch(url);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = (await resp.json()) as { items: ServiceItem[] };
            allServices = data.items;
        } catch (error) {
            console.error("搜索校园服务失败:", error);
        }
        renderChips();
        renderGrid();
    };
    el<HTMLButtonElement>("svc-search-btn").addEventListener("click", applySearch);
    searchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") applySearch();
    });

    el<HTMLButtonElement>("svc-detail-close").addEventListener("click", closeDetail);
    el<HTMLDivElement>("svc-detail-overlay").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) closeDetail();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !el<HTMLDivElement>("svc-detail-overlay").hidden) closeDetail();
    });

    void loadServices();
});
