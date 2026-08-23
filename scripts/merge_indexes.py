"""把多个知识库 collection 合并成一个，供线上只保留单一索引。

## 为什么要合并

``handlers/qa_workflow._build_retriever()`` 在索引数 > 1 时会走
``RouterRetriever`` + ``LLMSingleSelector``：每次提问先花一次 LLM 调用挑一个
索引，再只在那个索引里检索。``evals/run_index_topology_eval.py`` 在 76 题
golden 上实测（本机 + 自建网关）：

| 拓扑 | hit@1 | hit_rate@5 | MRR | 平均延迟 | 检索失败 |
|------|-------|-----------|-----|---------|---------|
| 多索引路由 | 63-82% | 74-95% | 0.66-0.86 | 9.5-24 s | 0-22% |
| 单一合并索引 | 75-82% | 93-96% | 0.83-0.86 | 0.7-0.9 s | 0 |

多索引那一行的区间就是问题本身：质量取决于选择器当次是否解析成功，失败时
生产端降级成"我还不知道"（``qa_workflow.retrieve`` 的 try/except），文档明明
在库里。合并后质量持平上限、延迟少一个数量级、失败率归零。

## 这个脚本做什么

直接搬 embedding，不重新摄取——向量是同一个模型算出来的，重算结果按定义
相同。按"文件名 + 正文 sha256"去重（与摄取管道 doc_id 同一判据）。

**不删除源 collection**：合并完成后请用 ``.env`` 的 ``EXCLUDED_COLLECTIONS``
把源 collection 排除出索引注册表，确认线上正常再决定是否删除数据。

用法::

    uv run python scripts/merge_indexes.py --dry-run
    uv run python scripts/merge_indexes.py --sources campus,campus-web --target campus-all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_APP_DIR = _REPO_ROOT / "backend" / "app"
if str(BACKEND_APP_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_APP_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="合并多个知识库 collection")
    parser.add_argument("--sources", help="逗号分隔的源 collection；缺省为除系统/排除项外的全部")
    parser.add_argument("--target", default="campus-all", help="目标 collection 名")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与条数，不写入")
    args = parser.parse_args()

    from handlers.index_crud import is_system_collection
    from handlers.vector_store import (
        get_or_create_collection,
        list_index_names,
        merge_collections,
    )

    available = [n for n in list_index_names() if not is_system_collection(n)]
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
        missing = [s for s in sources if s not in list_index_names()]
        if missing:
            print(f"[merge] 这些 collection 不存在: {missing}；当前可用: {available}")
            return 1
    else:
        sources = [n for n in available if n != args.target]

    print(f"[merge] 源: {sources}")
    print(f"[merge] 目标: {args.target}")
    for name in sources:
        print(f"  - {name}: {get_or_create_collection(name).count()} chunk")

    if args.dry_run:
        print("[merge] --dry-run，未写入任何数据。")
        return 0

    result = merge_collections(sources, args.target)
    total = get_or_create_collection(args.target).count()
    print(f"[merge] 写入 {result['added']} chunk，按内容去重跳过 {result['skipped']}，目标现有 {total} chunk。")
    print(
        "[merge] 下一步：在 backend/.env 里设置 "
        f"EXCLUDED_COLLECTIONS={','.join(sources)} 并重启，"
        "让线上只加载合并后的单一索引；确认无误后再决定是否删除源 collection。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
