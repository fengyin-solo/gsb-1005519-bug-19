"""环境监测站业务规则：状态流转、字段校验与筛选口径都收在这里。

缺测时段的处理约定（列表、详情、导出共用同一段取数逻辑）：
- 采集记录按采集时间排序保存，同一时段重复导入只留第一条；
- 既有的空测记录（一个测量字段都没有）保留为缺测占位，补录时被有效值顶掉；
- 任何展示口径都不许拿上一时刻的值顶替缺测时段，缺测就是空态；
- 缺测时段同步生成巡视检查的待办，补录落库后未开始的待办自动归档。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "environment"
PATROL_MODULE = "patrol"
REQUIRED_FIELDS = ["站点编号", "安装位置"]
MEASURE_FIELDS = ["辐照度", "风速", "风向", "积灰比", "环境温度"]
STATUS_ORDER = ["数据正常", "数据异常", "传感器故障", "已停用"]
ACTION_RULES = {"恢复正常": "数据正常", "标记异常": "数据异常", "停用站点": "已停用"}
NEGATIVE_ACTIONS = ["停用站点"]
RETRY_ACTION = "重试采集"
DEFAULT_INTERVAL_MINUTES = 60
TIME_FORMAT = "%Y-%m-%d %H:%M"
_TIME_CANDIDATES = (TIME_FORMAT, "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def _normalize_time(value: Any) -> str | None:
    """把采集时间规整成 YYYY-MM-DD HH:MM；取不到或格式不对就返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _TIME_CANDIDATES:
        try:
            return datetime.strptime(text, fmt).strftime(TIME_FORMAT)
        except ValueError:
            continue
    return None


def _is_placeholder(record: dict[str, Any]) -> bool:
    """空测记录：一个测量字段都没有。既有的空测记录没有「缺测」标记，按字段判断兼容。"""
    return all(str(record.get(field) or "").strip() == "" for field in MEASURE_FIELDS)


def _canonical_readings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一时段只留第一条有效记录；空测记录在没有有效记录时保留，有了就被顶掉。"""
    by_time: dict[str, dict[str, Any]] = {}
    for record in records:
        moment = _normalize_time(record.get("采集时间"))
        if moment is None:
            continue  # 兼容没有采集时间的脏数据：不进任何展示与导出口径
        record["采集时间"] = moment
        record["缺测"] = _is_placeholder(record)
        existing = by_time.get(moment)
        if existing is None or (existing["缺测"] and not record["缺测"]):
            by_time[moment] = record
    return [by_time[moment] for moment in sorted(by_time)]


def _missing_periods(entry: dict[str, Any], readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按采集间隔找出缺测时段：没有记录的时点、以及只有空测记录的时点都算缺测。"""
    if not readings:
        return []
    try:
        interval = int(entry.get("采集间隔分钟") or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_MINUTES
    step = timedelta(minutes=max(interval, 1))
    measured = {record["采集时间"] for record in readings if not record.get("缺测")}
    start = datetime.strptime(readings[0]["采集时间"], TIME_FORMAT)
    end = datetime.strptime(readings[-1]["采集时间"], TIME_FORMAT)
    missing: list[str] = []
    cursor = start
    while cursor <= end:
        moment = cursor.strftime(TIME_FORMAT)
        if moment not in measured:
            missing.append(moment)
        cursor += step
    groups: list[list[str]] = []
    for moment in missing:
        if groups:
            last = datetime.strptime(groups[-1][-1], TIME_FORMAT)
            if datetime.strptime(moment, TIME_FORMAT) - last == step:
                groups[-1].append(moment)
                continue
        groups.append([moment])
    return [
        {
            "开始": group[0],
            "结束": group[-1],
            "缺测点数": len(group),
            "时点": group,
            "说明": (
                f"{group[0]}~{group[-1]} 缺测 {len(group)} 个采集点，"
                "辐照度、风速、风向等读数缺失，等待补录，不会用上一时刻的值顶替"
            ),
        }
        for group in groups
    ]


def _display_fields(readings: list[dict[str, Any]]) -> dict[str, Any]:
    """列表与详情共用的取数口径：只反映当前（最新）时段，缺测就返回空，不拿上一时刻顶替。"""
    latest = readings[-1] if readings else None
    fields: dict[str, Any] = {
        "当前时段": latest["采集时间"] if latest else None,
        "当前缺测": latest is None or bool(latest.get("缺测")),
    }
    for field in MEASURE_FIELDS:
        fields[field] = latest.get(field) if latest and not latest.get("缺测") else None
    fields["通讯状态"] = latest.get("通讯状态") if latest else None
    return fields


class EnvironmentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        views = []
        for entry in store.rows(MODULE):
            self._refresh(entry)
            views.append(self._decorate(entry, with_readings=False))
        if keyword:
            views = [view for view in views if keyword in str(view.get("站点编号", ""))]
        if status:
            views = [view for view in views if view.get("status") == status]
        total = len(views)
        start = max(page - 1, 0) * size
        return views[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        self._refresh(entry)
        return self._decorate(entry, with_readings=True)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记站点：站点编号、安装位置缺失的单条不许保存，原因逐条写明。"""
        reasons: list[str] = []
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            reasons.append(f"缺少必填字段：{'、'.join(missing)}，单条不予保存")
        code = str(values.get("站点编号") or "").strip()
        rows = store.rows(MODULE)
        if code and any(str(row.get("站点编号") or "") == code for row in rows):
            reasons.append(f"站点编号 {code} 已登记过，同一站点不许重复保存")
        if reasons:
            return None, reasons
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry["站点编号"] = code
        entry["安装位置"] = str(values.get("安装位置") or "").strip()
        entry["采集间隔分钟"] = DEFAULT_INTERVAL_MINUTES
        entry["采集记录"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._decorate(entry, with_readings=True), []

    def ingest_readings(
        self, entry_id: int, readings: Any
    ) -> tuple[dict[str, Any] | None, str]:
        """导入采集记录（含中断后的补录）。

        逐条校验，不合格的单条拒绝并写明原因；同一时段重复导入只留第一条；
        空测记录记为缺测占位、允许重试补录；老记录按采集时间插回正确位置并落库。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"环境监测站 {entry_id} 不存在或已归档"
        if not isinstance(readings, list) or not readings:
            return None, "没有可导入的采集记录"
        station = str(entry.get("站点编号") or "")
        records = entry.setdefault("采集记录", [])
        known = [str(record.get("采集时间") or "") for record in records if record.get("采集时间")]
        latest_known = max(known, default=None)
        saved: list[str] = []
        backfilled: list[str] = []
        duplicates: list[str] = []
        anomalies: list[str] = []
        rejected: list[dict[str, Any]] = []
        for index, raw in enumerate(readings, start=1):
            if not isinstance(raw, dict):
                rejected.append({"序号": index, "原因": "记录不是键值对，单条不予保存"})
                continue
            code = str(raw.get("站点编号") or "").strip()
            if code and code != station:
                rejected.append({
                    "序号": index,
                    "采集时间": raw.get("采集时间"),
                    "原因": f"站点编号 {code} 与本站 {station} 不一致，单条不予保存",
                })
                continue
            moment = _normalize_time(raw.get("采集时间"))
            if moment is None:
                rejected.append({
                    "序号": index,
                    "采集时间": raw.get("采集时间"),
                    "原因": "缺少采集时间或格式不是 YYYY-MM-DD HH:MM，单条不予保存",
                })
                continue
            record: dict[str, Any] = {
                "采集时间": moment,
                "通讯状态": str(raw.get("通讯状态") or "").strip() or "在线",
            }
            for field in MEASURE_FIELDS:
                value = raw.get(field)
                if value is not None and str(value).strip() != "":
                    record[field] = value
            record["缺测"] = _is_placeholder(record)
            record["补录"] = bool(raw.get("补录")) or (latest_known is not None and moment < latest_known)
            existing = next((item for item in records if item.get("采集时间") == moment), None)
            if existing is not None:
                if _is_placeholder(existing) and not record["缺测"]:
                    # 补回中断期间取不到的历史值：原地落库，时段位置不变
                    record["补录"] = True
                    existing.clear()
                    existing.update(record)
                    backfilled.append(moment)
                else:
                    duplicates.append(moment)  # 同一时段重复导入只留第一条
                continue
            records.append(record)
            if record["缺测"]:
                anomalies.append(moment)  # 采集异常：记空测占位，允许重试
            elif record["补录"]:
                backfilled.append(moment)  # 中断期间的老时段：按采集顺序补回
            else:
                saved.append(moment)
        records.sort(key=lambda item: str(item.get("采集时间") or ""))
        entry["采集记录"] = _canonical_readings(records)  # 压实同一时段的重复记录
        gaps = self._refresh(entry)
        parts = [f"入库 {len(saved)} 条"]
        if backfilled:
            parts.append(f"补录 {len(backfilled)} 条已落库")
        if anomalies:
            parts.append(f"采集异常 {len(anomalies)} 条（已记空测，可重试）")
        if duplicates:
            parts.append(f"同一时段重复 {len(duplicates)} 条只留第一条")
        if rejected:
            parts.append(f"拒绝 {len(rejected)} 条")
        result = {
            "entry": self._decorate(entry, with_readings=True),
            "saved": saved,
            "backfilled": backfilled,
            "duplicates": duplicates,
            "anomalies": anomalies,
            "rejected": rejected,
            "缺测时段": gaps,
        }
        return result, "采集导入完成：" + "，".join(parts)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"环境监测站 {entry_id} 不存在或已归档"
        if action == RETRY_ACTION:
            gaps = self._refresh(entry)
            if not gaps:
                return self._decorate(entry, with_readings=True), "当前没有缺测时段，无需重试采集"
            periods = "、".join(f"{gap['开始']}~{gap['结束']}" for gap in gaps)
            return self._decorate(entry, with_readings=True), (
                f"已重新发起采集，待补时段：{periods}；补录按采集顺序接着写，同一时段重复导入只留第一条"
            )
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于环境监测站可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        self._refresh(entry)
        return self._decorate(entry, with_readings=True), f"环境监测站已{action}"

    def sync_all(self) -> None:
        """启动时全量刷新一遍：既有数据（含空测记录）的缺测待办不等首次读取就生成。"""
        for entry in store.rows(MODULE):
            self._refresh(entry)

    def export_rows(self) -> list[dict[str, Any]]:
        """导出清单：每个站点每个时段只出一行，重复时段只留第一条，缺测时段保留空测行并标注。"""
        rows: list[dict[str, Any]] = []
        for entry in store.rows(MODULE):
            self._refresh(entry)
            for record in _canonical_readings(entry.get("采集记录") or []):
                rows.append({
                    "站点编号": entry.get("站点编号"),
                    "安装位置": entry.get("安装位置"),
                    "采集时间": record.get("采集时间"),
                    **{field: record.get(field) for field in MEASURE_FIELDS},
                    "通讯状态": record.get("通讯状态"),
                    "缺测": bool(record.get("缺测")),
                    "补录": bool(record.get("补录")),
                })
        rows.sort(key=lambda row: (str(row["站点编号"]), str(row["采集时间"])))
        return rows

    def _decorate(self, entry: dict[str, Any], *, with_readings: bool) -> dict[str, Any]:
        """列表与详情共用的视图：积灰比等字段只从这里出，保证两边对得上。"""
        readings = _canonical_readings(entry.get("采集记录") or [])
        view = {key: value for key, value in entry.items() if key != "采集记录"}
        view.update(_display_fields(readings))
        gaps = _missing_periods(entry, readings)
        view["缺测时段"] = gaps
        view["缺测点数"] = sum(gap["缺测点数"] for gap in gaps)
        if with_readings:
            view["采集记录"] = readings
        return view

    def _refresh(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """重算缺测时段并同步巡视待办；幂等，读取与写入路径都可以调。"""
        readings = _canonical_readings(entry.get("采集记录") or [])
        gaps = _missing_periods(entry, readings)
        entry["abnormal"] = bool(gaps) or entry.get("status") == "数据异常"
        self._sync_patrol_todos(entry, gaps)
        return gaps

    def _sync_patrol_todos(self, entry: dict[str, Any], gaps: list[dict[str, Any]]) -> None:
        """缺测时段生成巡视待办：同一缺口只生成一次，补齐后未开始的待办自动归档。"""
        rows = store.rows(PATROL_MODULE)
        station = str(entry.get("站点编号") or "")
        prefix = f"ENV-GAP-{station}-"
        wanted = {f"{prefix}{gap['开始']}": gap for gap in gaps}
        for row in rows:
            key = str(row.get("关联编号") or "")
            if key.startswith(prefix) and key not in wanted and row.get("status") == "待巡视":
                row["status"] = "已归档"
                row["pending"] = False
                row["待办说明"] = f"{row.get('待办说明', '')}（缺测已补录，自动归档）"
        existing = {str(row.get("关联编号") or "") for row in rows}
        for key, gap in wanted.items():
            if key in existing:
                continue
            rows.append({
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "记录编号": self._next_patrol_code(rows),
                "巡视区域": entry.get("安装位置"),
                "巡视日期": date.today().isoformat(),
                "status": "待巡视",
                "巡视状态": "待巡视",
                "pending": True,
                "abnormal": False,
                "关联站点": station,
                "关联编号": key,
                "待办说明": (
                    f"环境监测站 {station} 在 {gap['开始']}~{gap['结束']} 缺测 "
                    f"{gap['缺测点数']} 个采集点，请现场核查采集与通讯装置，补录后本待办自动归档"
                ),
            })

    @staticmethod
    def _next_patrol_code(rows: list[dict[str, Any]]) -> str:
        suffixes = []
        for row in rows:
            code = str(row.get("记录编号") or "")
            if code.startswith("PATR-") and code[5:].isdigit():
                suffixes.append(int(code[5:]))
        return f"PATR-{max(suffixes, default=0) + 1:04d}"
