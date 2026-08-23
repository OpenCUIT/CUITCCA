# CUITCCA MVP 全面审计报告（2026-08-23）

> 审计目标：对照「AI Agent / AI 知识库」应用的 MVP 定位，盘点现状、判断复用/重构/缺失，给出分阶段实施计划。

---

## 1. 当前技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12+/3.13, FastAPI, Uvicorn（Modular Monolith，单进程） |
| AI 框架 | LlamaIndex（Workflow / QueryFusionRetriever / RouterRetriever / FunctionAgent） |
| 向量存储 | ChromaDB PersistentClient（5 collection，1024 维 bge-m3） |
| 嵌入 | BAAI/bge-m3（本地 HF，无需 API key），全局 `Settings.embed_model` 单实例 |
| 重排 | BAAI/bge-reranker-v2-m3（SentenceTransformerRerank，约 2.2GB） |
| 混合检索 | bm25s + jieba（BM25）+ Dense 向量，RRF 融合 |
| 文档解析 | 14 种格式注册表分派（pdfplumber / python-docx / pptx / openpyxl / olefile / BS4 / OCR 可选） |
| 关系存储 | SQLite WAL（4 表：access_stats / ip_visits / endpoint_visits / feedback） |
| 前端 | TypeScript + Vite MPA（无框架），marked + DOMPurify（vendor），3082 行统一 style.css |
| 测试 | pytest（631 个测试函数，coverage fail_under=90）、vitest、Playwright E2E |
| CI | GitHub Actions：ci（lint/typecheck/test/frontend/security）、e2e、evals（每周）、release |

## 2. 当前项目架构

```
backend/app/
├── main.py            # lifespan + 中间件（会话/限流/统计/CORS/静态托管）
├── router/            # 9 文件：index(19 端点) / graph_qa / graph_agent / graph_ask
│                      #        / graph_feedback / graph_session / manage / response
├── handlers/          # 10 文件：qa_workflow / hybrid_retriever / auto_router
│                      #          / qa_cache / index_crud / ingestion_pipeline
│                      #          / chunking / vector_store + parsers/(13)
├── agents/            # registry(ToolRegistry) / tools(4 工具) / agent_workflow(FunctionAgent)
├── configs/           # load_env(全部环境变量+热重载) / config(5 个 PromptTemplate)
│                      #   / llm_predictor(OpenAILike + bge-m3) / observability(OTel)
├── connectors/        # Web 爬虫子系统（sites.yaml 驱动，与 API 服务无关）
├── utils/             # security / upload / file / db / rerank / llama / llm_config / logger
└── dependencies/      # access_stats / index_dep
frontend/              # 5 页 MPA：index(聊天) / manage(知识库) / use_function / feed_back / config
data/                  # chroma_db(102MB) / corpus/web(546 md) / upload_files / app.db
信息搜集汇总/           # 257 个原始语料文件（txt 118 / pdf 55 / docx 25 / doc 17 …）
```

- 前端主入口：`POST /graph/ask_stream`（NDJSON 流）——语义缓存 → 自动路由（standard/agent）→ 生成 → 追问建议。
- 引用来源：不随答案返回，先存服务端 TTLCache，前端再调 `POST /graph/query_sources` 拉取。
- 会话历史：纯内存 TTLCache（200 会话 / 1h），重启即失。

## 3. 当前已有功能（对照 MVP 清单）

| 功能 | 状态 | 说明 |
|------|------|------|
| AI 对话（多轮/流式/Markdown/停止/清空/历史） | ✅ 完整 | NDJSON 流 + rAF 节流渲染 + localStorage 50 条历史 |
| 重新生成 | ⚠️ 半缺 | 仅网络失败时有重试按钮，正常回答无 regenerate |
| 消息级复制 | ⚠️ 半缺 | 只有引用来源卡片可复制，消息无复制按钮 |
| 代码高亮 | ❌ 缺 | vendor 无 highlight.js |
| 知识库上传（PDF/MD/TXT/DOCX 等 14 格式） | ✅ 超额 | 表格感知分块 + sha256 去重 + 增量 UPSERT |
| 知识库管理（索引 CRUD/摘要/chunk 编辑） | ✅ 完整 | 含节点级增删改、防抖自动保存 |
| 文档级管理列表（文件名/大小/chunk 数/状态） | ❌ 缺 | 只有 chunk 浏览器，无文档维度视图 |
| RAG Pipeline | ✅ 超额 | 混合检索+条件重排+条件改写+语义缓存，评测护航 |
| 引用来源展示 | ✅ 完整 | 折叠面板：文件名+分数+片段+复制 |
| RAG 防幻觉 | ✅ 基本完备 | prompt 约束 + 20 题拒答评测（幻觉率 0.2） |
| Agent + 4 工具 | ✅ 完整 | search_knowledge_base / list_knowledge_bases / get_document_chunks_by_source / get_current_datetime |
| Agent 状态展示 | ✅ 完整 | route 徽标 + 工具轨迹（运行中→✓/✗）+ 中文工具名映射 |
| 智能意图/路由 | ✅ 完整 | auto_router 按重排置信度分流，非显式意图分类 |
| **校园通知（数据/页面/工具）** | ❌ **全缺** | 无 announcements 表、无页面、无 Agent 工具 |
| **校园服务（数据/页面/工具）** | ❌ **全缺** | 同上 |
| 首页 | ⚠️ 简版 | 聊天即首页 + 6 个快捷问题；无 hero/功能入口 |
| Prompt 管理 | ✅ 基本集中 | 5 个在 configs/config.py，3 处散落（追问建议/QA 生成/索引摘要） |
| 模型抽象 | ✅ 够用 | OpenAILike 单协议覆盖所有 OpenAI 兼容模型 + env 热切换 + 在线配置页 |
| Embedding 抽象 | ⚠️ 单实现 | bge-m3 硬编码于 llm_predictor，无 provider 抽象（可接受） |
| AI 请求日志（model/tokens/latency） | ❌ 缺 | query_logger 只记文本；OTel 存在但默认关闭 |
| 安全 | ✅ 良好 | API key/限流/上传白名单/路径穿越防护/防投毒；prompt injection 仅 prompt 层软约束 |
| 测试 | ✅ 超额 | 631 测试函数 + 76 题 golden 检索评测（hit_rate 97.37%/MRR 0.858） |

## 4-7. AI / RAG / Agent / 知识库能力现状

**RAG（成熟，超出 MVP 要求）**：三步 QAWorkflow（condense→retrieve→synthesize）；BM25(bm25s+jieba)+Dense RRF 混合检索；条件 cross-encoder 重排（已知缺陷：触发条件实际恒为真，等价 always-on）；条件查询改写（阈值 0.45，实测仅 ~7% 触发）；语义缓存（auto 0.92/curated 0.82 双轨，LRU 驱逐）。

**Agent（成熟）**：FunctionAgent（原生 function calling）+ 4 工具 + 完整护栏（max_iterations=6 优雅收尾、90s 超时降级、区分“没查到”与“没跑完”的降级文案）。**缺口：没有结构化数据工具**（通知/服务），Demo 2“帮我看看最近有什么校园通知”无法演示。

**知识库（成熟）**：3 个生产索引（campus 806 / campus-web 1092 / campus-corpus 785 chunk，共 2683）；摄取管道带 sha256 去重、表格感知分块、噪声过滤。**缺口：管理页无文档级列表、无重新索引**。

**LLM/Embedding**：`OpenAILike` 单协议（当前 deepseek-v4-flash @ 自建网关），`.env` 热重载 + `/manage/llm-config` 在线换模型；embedding 固定 bge-m3。

## 8. UI/UX 现状

- 优点：成熟设计令牌系统（60+ CSS 变量、WCAG AA 实测校准）、完整暗色双轨、移动端抽屉侧栏、toast/loading/空状态/破坏性确认等交互细节到位。
- 风格：现代 AI 产品与轻拟物之间，非传统后台；但布局骨架仍是“侧栏+内容区”后台范式。
- 主要缺口：无多会话管理（只有单会话 50 条线性历史）、无消息级操作（复制/重新生成）、无代码高亮、Google Fonts 外链是可用性隐患。

## 9. 最值得保留的部分（直接复用）

1. **RAG 全链路**（hybrid_retriever / qa_workflow / rerank / auto_router / qa_cache）——评测数据支撑，是项目核心卖点
2. **Agent 编排 + 护栏**（agent_workflow / tools / registry）
3. **摄取管道**（parsers 注册表 / TableAwareSplitter / sha256 去重）
4. **聊天前端**（NDJSON 流式/工具轨迹/引用来源/反馈闭环）
5. **安全与工程化**（认证/限流/CI/评测/文档体系）

## 10. 最需要修改的部分（按优先级）

| # | 问题 | 位置 | 严重度 |
|---|------|------|--------|
| 1 | qa_cache 命中计数用 `collection.update` 只传 `{"hits"}`，chromadb 的 update 是整体替换 metadata → **条目命中一次后 answer 被抹空（自毁）** | handlers/qa_cache.py:166-176 | 🔴 用户可见 |
| 2 | 无结构化校园数据（announcements/campus_services），Demo 2 无法演示 | 全局缺口 | 🔴 MVP 核心 |
| 3 | 多个端点在事件循环上跑同步 Chroma/SQLite I/O（updateNode/deleteDoc/qa_cache 写路径等） | router/index.py, qa_cache.py | 🟡 并发卡顿 |
| 4 | `/index/{name}/getfile` 导出恒为空（from_vector_store 的 docstore 为空） | index_crud.py:272-285 | 🟡 功能坏死 |
| 5 | `/insertdoc` 绕过摄取管道（无分块/去重），`/evaluator` 绕过混合检索 | router/index.py:291/322 | 🟡 数据质量 |
| 6 | 会话历史无条数上限（长对话撑爆上下文）且纯内存 | qa_workflow.py:195 | 🟡 |
| 7 | ConditionalRerank 条件触发恒为真（已知未修）；`max_retrieval_iterations` 是空钩子 | utils/rerank.py, qa_workflow.py | 🟢 已知 |
| 8 | 死代码：exceptions/ 整包、audit_logger、graph_builder 空壳、llama.py 部分函数 | 多处 | 🟢 清理项 |
| 9 | Settings.text_splitter(512) 与摄取 chunk_size(1024) 不一致 | llm_predictor.py:53 | 🟢 |
| 10 | 单文件上传不更新索引摘要（与批量不一致） | router/index.py:148 | 🟢 |

## 11. MVP 缺失功能（本次实施范围）

1. **结构化校园数据**：announcements / campus_services 表 + 种子数据（语料里有 524 条带日期的校园资讯可转换）
2. **Agent 新工具**：search_announcements、search_campus_services（补齐 Demo 2 + 意图覆盖“通知/服务”）
3. **校园通知页面**：列表/搜索/分类/详情
4. **校园服务页面**：分类卡片/详情
5. **qa_cache 自毁 bug 修复**
6. （次优先，未在本轮实施）文档级知识库管理列表、AI 请求日志、消息复制/重新生成/代码高亮、多会话

## 12. 推荐的最终架构（Modular Monolith，不引入微服务）

在现有架构上**只加一个模块**，其余复用：

```
Frontend (Vite MPA)
  ├── index.html        聊天（已有）
  ├── announcements.html 校园通知（新增）
  ├── services.html     校园服务（新增）
  └── manage.html       知识库（已有）
        │
FastAPI
  ├── /graph/ask_stream   语义缓存→自动路由→QAWorkflow|Agent（已有）
  ├── /campus/*           通知/服务 CRUD+搜索（新增 router/campus.py）
  ├── /index/*            知识库管理（已有）
  │
  ├── CampusAgent（tools 扩到 6 个）
  │     ├── search_knowledge_base      （已有）
  │     ├── search_announcements       （新增：SQLite 查询）
  │     ├── search_campus_services     （新增：SQLite 查询）
  │     ├── list_knowledge_bases / get_document_chunks_by_source / get_current_datetime（已有）
  │     └──
  ├── RAG Pipeline（已有，不动）
  └── SQLite：+announcements +campus_services（utils/db.py 扩 schema）
```

## 13. 分阶段实施计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| 一 | 项目审计（本文件） | ✅ |
| 二 | P0 修复：qa_cache 自毁 bug | ✅ 本轮完成 |
| 三 | 结构化校园数据层（表+种子脚本+数据） | ✅ 本轮完成（524 通知 + 8 服务） |
| 四 | /campus API + Agent 新工具 + prompt 更新 + 测试 | ✅ 本轮完成（21 个新测试） |
| 五 | 前端通知页/服务页 + 导航接线 | ✅ 本轮完成（含浏览器实测） |
| 六 | 端到端验证（构建/测试/实测 Demo 2） | ✅ 本轮完成（ask_stream 实测 Agent 自动调用 search_announcements） |
| 七 | 知识库文档级管理列表 + 重新索引 | ✅ 本轮完成（含文本录入改走摄取管道、导出修复） |
| 八 | Chat UI 补齐（消息复制/重新生成/代码高亮/多会话） | ✅ 本轮完成 |
| 九 | AI 请求日志（model/tokens/latency/tool_calls） | ✅ 本轮完成（JSONL + /manage/ai-metrics） |
| 十 | 死代码清理 + 阻塞 I/O 修复 + 首页产品化 | ⚠️ 部分完成（死代码清理、阻塞 I/O 已修；首页产品化未做） |

**优先级依据**（项目定位）：AI 能力/RAG/Agent/Tool Calling ★★★★★ → 先补结构化数据工具链（Demo 2 是验收清单第 10 条“AI 可以调用真实数据”的直接体现）；校园功能数量 ★★☆☆☆ → 页面做简但完整，不做大而全。
