"""环境监测站接口：维护环境监测站，并统一采集取数、补传重试与缺测时段口径。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.environment import EnvironmentService

router = APIRouter(prefix="/api/environment", tags=["环境监测站"])

service = EnvironmentService()

LIST_FIELDS = ["站点编号", "安装位置", "辐照度", "环境温度", "风速", "风向", "积灰比", "通讯状态"]
STATUSES = ["数据正常", "数据异常", "传感器故障", "已停用"]


class IngestPayload(BaseModel):
    """采集报文：按采集顺序排列的一批读数，可带「补传」标记，支持重试。"""

    records: list[dict[str, Any]] = Field(default_factory=list)
    retry: bool = False


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按站点编号检索"),
    status: str | None = Query(default=None, description="数据正常、数据异常、传感器故障、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按站点编号与状态过滤环境监测站列表；没有数据时返回空页，不报错。

    辐照度、风速、风向等取不到的时刻返回 null 与缺测说明，不沿用上一时刻读数。
    """
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/gaps")
def list_gaps(
    station: str | None = Query(default=None, description="按站点编号过滤缺测时段"),
) -> dict[str, Any]:
    """缺测时段清单：每条都带空态说明与对应巡视待办；补齐后标记已补齐。"""
    items = service.list_gaps(station_code=station)
    return {"module": "environment", "total": len(items), "items": items}


@router.post("/readings/ingest")
def ingest_readings(payload: IngestPayload) -> dict[str, Any]:
    """接收实时采集或通讯恢复后的补传报文。

    - 站点编号/安装位置缺失的单条不许保存，逐条写明原因，允许整批复试；
    - 采集异常的单条不落库，等重试，不用上一时刻的值顶替；
    - 同一站点同一采集时间重复导入只留第一条；
    - 补传按采集时间插回时间轴，缺测时段随之核销并联动巡视待办。
    """
    if not payload.records:
        raise HTTPException(status_code=400, detail="本批没有任何采集记录，请检查报文后重试")
    return service.ingest_readings(payload.records, retry=payload.retry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出环境监测站清单：站点与采集时段都已去重，缺测时段附空态说明。"""
    return service.export_entries()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条环境监测站明细；不存在时给出可读的错误说明。

    明细与列表走同一取数投影，积灰比等字段口径保持一致。
    """
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"环境监测站 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条环境监测站，缺站点编号或安装位置时说明原因，不许静默保存。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}，该条未保存")
    return ActionResult(ok=True, message="环境监测站已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条环境监测站执行恢复正常、标记异常、停用站点；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
