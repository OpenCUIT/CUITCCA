"""结构化校园数据（通知公告 / 校园服务）的只读 API。

跟 /index（向量知识库）互补：这里是"能按时间排序、按分类筛选的列表数据"，
为校园通知/校园服务页面和 Agent 的通知/服务工具提供数据源。

只做 GET：数据由 scripts/seed_campus_data.py 离线灌入，不提供在线写入
（MVP 不做通知管理后台，避免为低频功能扩攻击面）。

SQLite 查询都是毫秒级本地读，但仍然是同步阻塞调用——统一 asyncio.to_thread
卸载，不占用事件循环（与 /manage 统计端点同一约定；那里是历史遗留直接同步
调用，这里是新代码按正确姿势写）。
"""
from __future__ import annotations

import asyncio

import configs.load_env as load_env
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from utils import campus_data
from utils.security import require_api_key_if_configured

campus_app = APIRouter(dependencies=[Depends(require_api_key_if_configured)])


@campus_app.get("/announcements")
async def list_announcements(
    query: str | None = Query(None, description="关键词，匹配标题/摘要/正文"),
    category: str | None = Query(None, description="栏目分类"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    result = await asyncio.to_thread(
        campus_data.list_announcements,
        load_env.db_path,
        query=query,
        category=category,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(result)


@campus_app.get("/announcements/categories")
async def announcement_categories():
    return JSONResponse(
        await asyncio.to_thread(campus_data.list_announcement_categories, load_env.db_path)
    )


@campus_app.get("/announcements/{ann_id}")
async def get_announcement(ann_id: int):
    item = await asyncio.to_thread(campus_data.get_announcement, load_env.db_path, ann_id)
    if item is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    return JSONResponse(item)


@campus_app.get("/services")
async def list_services(
    query: str | None = Query(None, description="关键词，匹配名称/检索别名/内容"),
    category: str | None = Query(None, description="服务分类"),
):
    items = await asyncio.to_thread(
        campus_data.list_campus_services,
        load_env.db_path,
        query=query,
        category=category,
    )
    return JSONResponse({"total": len(items), "items": items})


@campus_app.get("/services/categories")
async def service_categories():
    return JSONResponse(
        await asyncio.to_thread(campus_data.list_service_categories, load_env.db_path)
    )


@campus_app.get("/services/{service_id}")
async def get_service(service_id: int):
    item = await asyncio.to_thread(campus_data.get_campus_service, load_env.db_path, service_id)
    if item is None:
        raise HTTPException(status_code=404, detail="服务不存在")
    return JSONResponse(item)


@campus_app.get("/stats")
async def campus_stats():
    """知识库管理页 / 首页统计卡：结构化数据总量。"""
    return JSONResponse(
        await asyncio.to_thread(campus_data.campus_data_stats, load_env.db_path)
    )
