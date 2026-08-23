# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 结构化校园数据层：SQLite `announcements` / `campus_services` 表 + 幂等种子脚本
  `scripts/seed_campus_data.py`（爬取语料 524 条官方资讯 + 8 项人工整理办事指南）。
- `/campus` 只读 API：通知公告列表/搜索/分类/详情、校园服务列表/搜索/详情、总量统计。
- Agent 新工具 `search_announcements` / `search_campus_services`（共 6 个工具），
  Agent system prompt 增加按问题形态选工具的规则。

- 知识库文档级管理：`GET /index/{name}/documents`（按 ref_doc_id 聚合的文档列表：
  文件名/chunk 数/大小/来源）+ `POST /index/{name}/reindex`（单文档重新索引），
  管理页新增「文档」面板。
- 多会话：聊天页会话工具条（新建/切换/重命名/删除，会话元数据存浏览器本地），
  服务端上下文按 `conversation_id` 隔离，反馈端点同步使用同一复合会话键。
- 消息级操作：整条回答复制、最后一条回答重新生成（`pop_last` 撤回上一轮历史 +
  `skip_cache` 跳过语义缓存，否则逐字重发必然命中缓存拿回一字不差的旧答案）。
- 代码块高亮与复制：本地 vendor 打包的 highlight.js（不外链 CDN）。
- AI 请求日志 `utils/ai_metrics.py`：按请求记录 model / 路由模式 / 缓存命中 /
  首字与总延迟 / 工具调用 / 检索条数 / token 估算，落 `log/ai_metrics.jsonl`；
  `GET /manage/ai-metrics` 提供分位与分布聚合。

### Changed
- `/index/{name}/insertdoc` 文本录入改走摄取管道（表格感知分块 + 内容 sha256
  去重 + UPSERTS），不再直接 `insert_nodes` 绕过管道。
- 索引导出 `export_index_to_file` 改为直接从 Chroma 读取——原实现依赖
  `from_vector_store` 的空 docstore，导出文件恒为空。
- 索引节点增删改、文本录入、导出等端点的同步 Chroma/磁盘 I/O 统一
  `asyncio.to_thread` 卸载，不再阻塞事件循环。

### Removed
- 死代码清理：`backend/app/exceptions/` 整包、`utils/logger.py` 的 `audit_logger`、
  `index_crud` 中无调用方的 `convert_index_to_file` / `citf` / `get_docs_from_index`。

### Fixed
- 语义缓存命中计数自毁：`qa_cache.lookup` 用 chromadb `update` 只传 `{"hits": N}`，
  整体替换 metadata 把 answer/kind 一并抹掉，条目命中一次后返回空答案；
  现在带上完整 metadata 覆盖，并补回归测试。

## [0.3.0] - 2026-07-24

### Added
- Frontend modernization: Vite + TypeScript dev/build pipeline with hot module replacement.
- Type-safe API interfaces in `frontend/src/types/api.ts`.
- Production build step outputting to `backend/app/static/` for FastAPI static serving.
- Makefile targets: `frontend-install`, `frontend-dev`, `frontend-build`.

### Changed
- Frontend source migrated from plain JS to TypeScript (`sidebar.ts`, `chat.ts`, `manage.ts`, `feedback.ts`, `feed_back.ts`).
- Inline `onclick`/`oninput` handlers replaced with `addEventListener` bindings for ES module compatibility.

### Fixed
- XLSX file parsing support via explicit `openpyxl` usage in `utils/file.py`.
- PromptTemplate parameter error in `router/index.py` (was passing object instead of template string).
- Upload file storage now uses `index_id` subdirectory with failure rollback.
- `insert_docs` now uses index-level lock and invalidates hybrid retriever cache.
- `access_stats_lock` moved to `dependencies/manage.py` to resolve circular import.
- Missing `get_client_ip` function added to `utils/security.py`.

## [0.2.0] - 2026-07-18

### Added
- QAWorkflow: migrated all chat endpoints to LlamaIndex Workflow primitives (`condense_question -> retrieve -> synthesize`) with streaming support.
- Hybrid retrieval: BM25 (jieba + bm25s) + dense vector RRF fusion, default-enabled via `HYBRID_RETRIEVAL_ENABLED`.
- Conditional cross-encoder rerank (bge-reranker-v2-m3), default-enabled via `RERANK_ENABLED`, with eval-validated parameters (`recall_k=20`, `top_n=5`, `score_threshold=0.75`).
- Incremental ingestion pipeline with sha256 content dedup and conflict resolution (same-directory update vs cross-directory conflict).
- OpenTelemetry observability (OpenInference + OTLP), env-gated via `OBSERVABILITY_ENABLED`.
- Hybrid retrieval eval framework (`evals/run_hybrid_eval.py`) with A/B/C baselines.
- Rerank A/B eval (`evals/run_rerank_eval.py`) and workflow retrieval eval (`evals/run_workflow_retrieval_eval.py`).

### Changed
- Retrieval layer unified through `build_retriever_for_index()` entry point.
- `handlers/graph_builder.py` reduced to `summary_index()` only; legacy `CondenseQuestionChatEngine`/`RouterQueryEngine` removed.
- Rate limiting refined to cover only LLM query endpoints.

### Fixed
- P0 CI quality gate: fixed `ConditionalRerankPostprocessor` lazy-loading race condition.
- docx/xlsx upload regression: added `docx2txt` and `openpyxl` as explicit dependencies.

## [0.1.0] - 2025-07-10

### Added
- Initial RAG Q&A system with LlamaIndex + Chroma vector store.
- Multi-turn chat with streaming token output (`/graph/chat_stream`).
- Markdown rendering with `marked.js` + DOMPurify, with expandable citation sources.
- Knowledge base management: create/delete indexes, upload documents (PDF/DOCX/TXT/MD/CSV/XLSX), add/remove nodes.
- QA generation from documents (`/index/{name}/upload_file_by_QA`).
- Graph query endpoints (`/graph/query`, `/graph/query_stream`).
- Conversation history persistence in `localStorage`.
- Dark mode via `prefers-color-scheme`.
- Feedback collection page (`/manage/feedback`).
- Usage guide page.
- CI pipeline: lint (`ruff`), typecheck (`mypy`), test (`pytest` + coverage gate 90%), security (`pip-audit`, `bandit`).
