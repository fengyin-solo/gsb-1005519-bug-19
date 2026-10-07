"""环境监测站接口：维护环境监测站，覆盖采集导入、补录重试、恢复正常、标记异常、停用站点等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.environment import EnvironmentService

router = APIRouter(prefix="/api/environment", tags=["环境监测站"])

service = EnvironmentService()

LIST_FIELDS = ["站点编号", "安装位置", "当前时段", "辐照度", "风速", "风向", "积灰比", "通讯状态", "缺测时段"]
STATUSES = ["数据正常", "数据异常", "传感器故障", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按站点编号检索"),
    status: str | None = Query(default=None, description="数据正常、数据异常、传感器故障、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按站点编号与状态过滤环境监测站列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出环境监测站采集清单：同一时段只留第一条，缺测时段保留空测行并标注。"""
    items = service.export_rows()
    return {"module": "environment", "total": len(items), "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条环境监测站明细（含采集记录与缺测时段）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"环境监测站 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条环境监测站；站点编号、安装位置缺失的单条不许保存，并写明原因。"""
    entry, reasons = service.create_entry(payload.values)
    if reasons:
        return ActionResult(ok=False, message="；".join(reasons))
    return ActionResult(ok=True, message="环境监测站已登记", entry=entry)


@router.post("/{entry_id}/readings")
def ingest_readings(entry_id: int, payload: EntryPayload) -> dict[str, Any]:
    """导入采集记录（含通讯恢复后的补录）：逐条校验、同一时段只留第一条、空测记为缺测可重试。"""
    result, message = service.ingest_readings(entry_id, payload.values.get("readings"))
    if result is None:
        return {"ok": False, "message": message}
    return {"ok": True, "message": message, **result}


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条环境监测站执行重试采集、恢复正常、标记异常、停用站点；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
