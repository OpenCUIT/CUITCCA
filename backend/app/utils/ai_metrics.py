"""AI 请求日志：按请求记录 model / 路由模式 / 延迟 / 工具调用 / 检索条数 /
token 估算，落 JSONL（log/ai_metrics.jsonl），供 /manage/ai-metrics 聚合查询。

为什么不用 OTel 替代：OTel 链路（configs/observability.py）默认关闭且面向
外部 collector，日常演示要的是"一条 curl 就能看到的成本/性能账本"，这里
用最朴素的 JSONL 文件 + 聚合端点。两者不冲突——字段命名对齐 OpenInference
语义（llm.token_count / llm.latency），以后要接 OTel 可以直接复用。

token 是估算值（answer_chars/2，中文约 1 字 ≈ 0.5-1.3 token 的中位经验值）：
OpenAI 兼容网关在流式响应里不一定回传 usage，拿不到精确计数时宁可记
"估算"也不留空——成本监控要的是量级正确，不是精确到个位。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime

import configs.load_env as load_env

logger = logging.getLogger(__name__)

_METRICS_FILE = "ai_metrics.jsonl"
# 聚合端点读多少条近期记录（文件本身是 RotatingFileHandler 之外的手写
# JSONL，这里只顺读尾部，避免大文件全量加载）
_AGGREGATE_WINDOW = 500


def _current_model() -> str:
    # OPENAI_MODEL 是 reload_env_variables() 里从 os.environ 读的局部值，
    # 不挂在 load_env 模块属性上——这里直接读环境变量，跟 load_env 同源。
    return os.environ.get("OPENAI_MODEL", "unknown")


def _metrics_path() -> str:
    return os.path.join(load_env.LOG_PATH, _METRICS_FILE)


def _append_sync(entry: dict) -> None:
    os.makedirs(load_env.LOG_PATH, exist_ok=True)
    with open(_metrics_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def log_request(**fields) -> None:
    """追加一条请求记录（best-effort：失败只打日志，绝不影响主流程）。"""
    import asyncio

    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": _current_model(),
        **fields,
    }
    try:
        await asyncio.to_thread(_append_sync, entry)
    except Exception:
        logger.warning("AI 请求日志写入失败（best-effort）。", exc_info=True)


def estimate_tokens(text: str) -> int:
    """中文为主的文本 token 估算（见模块 docstring 的取舍说明）。"""
    return (len(text or "") + 1) // 2


def read_recent(limit: int = _AGGREGATE_WINDOW) -> list[dict]:
    """读最近 limit 条记录（尾部）。"""
    try:
        with open(_metrics_path(), encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
    except FileNotFoundError:
        return []


def summarize(limit: int = _AGGREGATE_WINDOW) -> dict:
    """聚合最近 N 条：请求数、模式分布、缓存命中率、延迟分位、token 合计。"""
    entries = read_recent(limit)
    if not entries:
        return {"requests": 0, "window": limit, "model": _current_model()}

    latencies = sorted(e.get("latency_ms") or 0 for e in entries)
    by_mode: dict[str, int] = {}
    total_tokens = 0
    cache_hits = 0
    tool_calls = 0
    for e in entries:
        mode = e.get("mode") or "unknown"
        by_mode[mode] = by_mode.get(mode, 0) + 1
        if mode == "cache":
            cache_hits += 1
        total_tokens += e.get("est_completion_tokens") or 0
        tool_calls += e.get("tool_calls") or 0

    def pct(p: float) -> int:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))]

    return {
        "requests": len(entries),
        "window": limit,
        "model": entries[-1].get("model") or _current_model(),
        "by_mode": by_mode,
        "cache_hit_rate": round(cache_hits / len(entries), 4),
        "tool_calls_total": tool_calls,
        "est_tokens_total": total_tokens,
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies)),
            "p50": pct(0.50),
            "p95": pct(0.95),
        },
        "recent": entries[-10:],
    }


def _now_ms() -> float:
    return time.monotonic() * 1000
