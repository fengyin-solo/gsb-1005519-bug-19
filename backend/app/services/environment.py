"""环境监测站业务规则：采集取数、缺测判定与补传回填都收在这里。

口径约定（列表与详情必须走同一条取数链路）：
- 任一时刻的读数只来自该时刻的采集记录；辐照度、风速、风向等整段取不到时
  保持空值并标注「缺测」，绝不用上一时刻的读数顶替。
- 采集记录按「站点编号 + 采集时间」去重，同一时段重复导入只留第一条。
- 通讯中断恢复后按采集顺序逐条补传，按采集时间插回时间轴，不要求整段重发；
  补回的值落库，老记录同样按采集时间回填。
- 缺测时段生成空态说明，并在巡视检查里生成待办；补齐后自动核销。
- 站点编号、安装位置缺失的单条记录不许保存，逐条写明原因并允许重试。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "environment"
READING_MODULE = "environment_reading"
GAP_MODULE = "environment_gap"

# 站点建档只强制身份字段；测量项允许缺测（缺测不等于建档失败）。
REQUIRED_FIELDS = ["站点编号", "安装位置"]
METRIC_FIELDS = ["辐照度", "环境温度", "风速", "风向", "积灰比"]
STATUS_ORDER = ["数据正常", "数据异常", "传感器故障", "已停用"]
ACTION_RULES = {"恢复正常": "数据正常", "标记异常": "数据异常", "停用站点": "已停用"}
NEGATIVE_ACTIONS = ["停用站点"]

# 缺测时段的统一空态文案，列表、详情、导出与巡视待办共用。
GAP_EMPTY_HINT = "通讯中断，该时段辐照度、风速、风向等整段缺测，未沿用上一时刻读数"
DEFAULT_CADENCE = timedelta(hours=1)


def _parse_time(value: Any) -> datetime | None:
    """宽松解析采集时间，兼容 'YYYY-MM-DD HH:MM' 与 ISO 形式。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _seq(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class EnvironmentService:
    _bootstrapped = False

    # ------------------------------------------------------------------ 取数

    def _ensure_bootstrapped(self) -> None:
        """种子数据只有采集记录；首次使用时按时间轴补算缺测时段与巡视待办。"""
        if EnvironmentService._bootstrapped:
            return
        EnvironmentService._bootstrapped = True
        codes = {str(row.get("站点编号", "")).strip() for row in store.rows(READING_MODULE)}
        for code in sorted(c for c in codes if c):
            self._sync_gaps(code)

    def _readings_of(self, station_code: str) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows(READING_MODULE)
            if str(row.get("站点编号", "")).strip() == station_code
        ]

    def _gaps_of(self, station_code: str) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows(GAP_MODULE)
            if str(row.get("站点编号", "")).strip() == station_code
        ]

    def _station_view(self, station: dict[str, Any]) -> dict[str, Any]:
        """统一取数投影：列表行和详情页都从这里取，口径天然一致（含积灰比）。"""
        view = dict(station)
        code = str(station.get("站点编号", "")).strip()
        readings = self._readings_of(code)
        latest = max(readings, key=lambda r: _parse_time(r.get("采集时间")) or datetime.min, default=None)

        missing: list[str] = []
        if latest is None:
            view["最近采集时间"] = None
            view["通讯状态"] = view.get("通讯状态") or "未采集"
        else:
            view["最近采集时间"] = latest.get("采集时间")
            # 只取该时刻自己的值；空就是空，不回看上一时刻。
            for field in METRIC_FIELDS:
                value = latest.get(field)
                if value is None or str(value).strip() == "":
                    missing.append(field)
                    view[field] = None
                else:
                    view[field] = value
            state = latest.get("状态")
            view["通讯状态"] = "缺测" if state == "缺测" else latest.get("通讯状态") or view.get("通讯状态") or "正常"
        open_gaps = [g for g in self._gaps_of(code) if not g.get("已补齐")]
        view["缺测字段"] = missing
        view["未补齐缺测时段数"] = len(open_gaps)
        view["空态说明"] = GAP_EMPTY_HINT if open_gaps or missing else ""
        return view

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self._ensure_bootstrapped()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("站点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._station_view(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        self._ensure_bootstrapped()
        station = store.find(MODULE, entry_id)
        if station is None:
            return None
        view = self._station_view(station)
        code = str(station.get("站点编号", "")).strip()
        timeline = sorted(
            self._readings_of(code),
            key=lambda r: _parse_time(r.get("采集时间")) or datetime.min,
        )
        view["采集时序"] = [self._reading_view(row) for row in timeline]
        view["缺测时段"] = [dict(gap) for gap in self._gaps_of(code)]
        return view

    def _reading_view(self, reading: dict[str, Any]) -> dict[str, Any]:
        item = {
            "id": reading.get("id"),
            "站点编号": reading.get("站点编号"),
            "安装位置": reading.get("安装位置"),
            "采集时间": reading.get("采集时间"),
            "通讯状态": reading.get("通讯状态", "正常"),
            "状态": reading.get("状态", "正常"),
            "补传": bool(reading.get("补传")),
            "缺测字段": [],
        }
        for field in METRIC_FIELDS:
            value = reading.get(field)
            if value is None or str(value).strip() == "":
                item[field] = None
                item["缺测字段"].append(field)
            else:
                item[field] = value
        item["空态说明"] = GAP_EMPTY_HINT if item["缺测字段"] else ""
        return item

    # ------------------------------------------------------------------ 建档

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": _next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in METRIC_FIELDS + ["通讯状态"]:
            entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"环境监测站 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于环境监测站可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"环境监测站已{action}"

    # ------------------------------------------------------------ 采集/补传

    def ingest_readings(
        self, batch: list[dict[str, Any]], *, retry: bool = False
    ) -> dict[str, Any]:
        """按采集顺序接收一批报文。

        返回 saved/duplicated/backfilled/failed 四类结果：failed 里的单条可以
        原样重试；duplicated 说明同一时段已导入过，保留第一条不动。
        """
        self._ensure_bootstrapped()
        reading_rows = store.rows(READING_MODULE)
        saved: list[dict[str, Any]] = []
        duplicated: list[dict[str, Any]] = []
        backfilled: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        touched_stations: set[str] = set()

        existing_keys = {
            (str(r.get("站点编号", "")).strip(), str(r.get("采集时间", "")).strip())
            for r in reading_rows
        }
        batch_keys: set[tuple[str, str]] = set()

        # 按采集顺序（采集时间，其次报文中的采集序号）逐条落库。
        def order_key(item: dict[str, Any]) -> tuple[datetime, int]:
            return (
                _parse_time(item.get("采集时间")) or datetime.max,
                _seq(item.get("采集序号")),
            )

        for index, raw in enumerate(sorted(batch, key=order_key)):
            item = raw if isinstance(raw, dict) else {}
            code = str(item.get("站点编号", "")).strip()
            location = str(item.get("安装位置", "")).strip()
            stamp = str(item.get("采集时间", "")).strip()

            reason = ""
            if not code:
                reason = "缺少必填字段：站点编号"
            elif not location:
                reason = "缺少必填字段：安装位置"
            elif not stamp or _parse_time(stamp) is None:
                reason = "采集时间缺失或格式无法识别，需形如 2026-10-07 09:00"
            elif str(item.get("采集异常", "")).strip() in ("1", "true", "True", "是") or item.get("采集异常") is True:
                # 采集端自己标注的异常：不落库、不顶替，等重试拿到真值。
                reason = f"采集异常：{item.get('异常说明') or '现场取值失败'}，请重试，未沿用上一时刻读数"
            if reason:
                failed.append({"序号": index, "站点编号": code, "采集时间": stamp, "原因": reason, "报文": item})
                continue

            dedupe_key = (code, stamp)
            if dedupe_key in existing_keys or dedupe_key in batch_keys:
                # 同一时段重复导入：只留第一条。
                duplicated.append({"站点编号": code, "采集时间": stamp})
                continue
            batch_keys.add(dedupe_key)

            latest_time = max(
                (_parse_time(r.get("采集时间")) for r in self._readings_of(code)),
                default=None,
            )
            is_backfill = bool(item.get("补传")) or (
                latest_time is not None and _parse_time(stamp) < latest_time
            )

            reading = {
                "id": _next_id(reading_rows),
                "站点编号": code,
                "安装位置": location,
                "采集时间": stamp,
                "采集序号": _seq(item.get("采集序号")),
                "补传": is_backfill,
                "通讯状态": item.get("通讯状态") or ("补传中" if is_backfill else "正常"),
            }
            empty_metrics: list[str] = []
            for field in METRIC_FIELDS:
                value = item.get(field)
                if value is None or str(value).strip() == "":
                    # 兼容既有的空测记录：空值原样保留，严禁拿上一时刻顶替。
                    reading[field] = None
                    empty_metrics.append(field)
                else:
                    reading[field] = str(value).strip()
            reading["状态"] = "缺测" if empty_metrics else "正常"
            reading_rows.append(reading)
            existing_keys.add(dedupe_key)
            touched_stations.add(code)

            summary = self._reading_view(reading)
            saved.append(summary)
            if is_backfill:
                backfilled.append(summary)

        gaps: list[dict[str, Any]] = []
        patrol_todos: list[dict[str, Any]] = []
        for code in sorted(touched_stations):
            station_gaps, station_todos = self._sync_gaps(code)
            gaps.extend(station_gaps)
            patrol_todos.extend(station_todos)

        message_parts = [f"入库 {len(saved)} 条"]
        if backfilled:
            message_parts.append(f"补传回填 {len(backfilled)} 条")
        if duplicated:
            message_parts.append(f"重复时段跳过 {len(duplicated)} 条")
        if failed:
            message_parts.append(f"{len(failed)} 条采集失败待重试")
        return {
            "ok": not failed,
            "retry": retry,
            "message": "，".join(message_parts),
            "saved": saved,
            "duplicated": duplicated,
            "backfilled": backfilled,
            "failed": failed,
            "gaps": gaps,
            "patrol_todos": patrol_todos,
        }

    def list_gaps(self, *, station_code: str | None = None) -> list[dict[str, Any]]:
        self._ensure_bootstrapped()
        gaps = store.rows(GAP_MODULE)
        if station_code:
            gaps = [g for g in gaps if str(g.get("站点编号", "")).strip() == station_code]
        return sorted(gaps, key=lambda g: (str(g.get("站点编号", "")), str(g.get("开始时间", ""))))

    def _sync_gaps(self, station_code: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按时间轴重算某站点的缺测时段，并联动巡视待办的生成与核销。

        相邻采集记录的时间跨度明显大于该站采集节拍时，中间即为缺测时段；
        补传把空档填满后，对应时段标记已补齐，巡视待办同步归档。
        """
        timeline = sorted(
            ((_parse_time(r.get("采集时间")), r) for r in self._readings_of(station_code)),
            key=lambda pair: pair[0] or datetime.min,
        )
        timeline = [(ts, r) for ts, r in timeline if ts is not None]
        cadence = DEFAULT_CADENCE
        # 节拍取相邻间隔的众数（最常见的正常采样周期）：既不会被中途的一条密点
        # 带偏（造出「开始即结束」的假时段），也不会被中断形成的长间隔稀释
        # （漏掉真正的缺测档）。
        counts: dict[timedelta, int] = {}
        for (start, _), (end, _) in zip(timeline, timeline[1:]):
            delta = end - start
            if delta > timedelta(0):
                counts[delta] = counts.get(delta, 0) + 1
        if counts:
            cadence = max(counts, key=lambda key: (counts[key], -key.total_seconds()))

        expected: list[tuple[str, str]] = []
        for (start, _), (end, _) in zip(timeline, timeline[1:]):
            gap_start_ts = start + cadence
            gap_end_ts = end - cadence
            if end - start > cadence * 1.5 and gap_end_ts >= gap_start_ts:
                gap_start = gap_start_ts.strftime("%Y-%m-%d %H:%M")
                gap_end = gap_end_ts.strftime("%Y-%m-%d %H:%M")
                expected.append((gap_start, gap_end))

        location = ""
        if timeline:
            location = str(timeline[-1][1].get("安装位置", "")).strip()
        else:
            station = next(
                (s for s in store.rows(MODULE) if str(s.get("站点编号", "")).strip() == station_code),
                None,
            )
            location = str(station.get("安装位置", "")).strip() if station else ""

        gaps_rows = store.rows(GAP_MODULE)
        patrol_rows = store.rows("patrol")
        current = {(g[0], g[1]) for g in expected}
        touched: dict[int, dict[str, Any]] = {}
        todos: list[dict[str, Any]] = []

        for gap in self._gaps_of(station_code):
            key = (str(gap.get("开始时间", "")), str(gap.get("结束时间", "")))
            if key not in current and not gap.get("已补齐"):
                # 补传已经把这段填上：核销缺测与巡视待办。
                gap["已补齐"] = True
                gap["空态说明"] = f"该时段已按采集顺序补传回填（{GAP_EMPTY_HINT}）"
                patrol_id = gap.get("巡视待办ID")
                if patrol_id:
                    todo = next((p for p in patrol_rows if int(p.get("id", 0)) == int(patrol_id)), None)
                    if todo and todo.get("status") != "已归档":
                        todo["status"] = "已归档"
                        todo["pending"] = False
                        todo["巡视状态"] = "已归档"
            touched[int(gap["id"])] = gap

        for start, end in expected:
            existing = next(
                (
                    g
                    for g in self._gaps_of(station_code)
                    if str(g.get("开始时间", "")) == start and str(g.get("结束时间", "")) == end
                ),
                None,
            )
            if existing is not None:
                touched[int(existing["id"])] = existing
                continue
            gap = {
                "id": _next_id(gaps_rows),
                "站点编号": station_code,
                "安装位置": location,
                "开始时间": start,
                "结束时间": end,
                "已补齐": False,
                "空态说明": f"{station_code} {start}~{end} {GAP_EMPTY_HINT}",
            }
            todo = {
                "id": _next_id(patrol_rows),
                "status": "待巡视",
                "pending": True,
                "abnormal": True,
                "记录编号": f"PATROL-GAP-{station_code}-{start.replace('-', '').replace(':', '').replace(' ', '')}",
                "巡视区域": f"{station_code} {location}".strip(),
                "巡视日期": start[:10],
                "巡视人员": "",
                "发现缺陷数": "",
                "红外测温结果": "",
                "接线端子温度": "",
                "巡视状态": "待巡视",
                "来源": "环境监测站缺测时段",
                "待办说明": gap["空态说明"],
            }
            patrol_rows.append(todo)
            gap["巡视待办ID"] = todo["id"]
            gap["巡视待办编号"] = todo["记录编号"]
            gaps_rows.append(gap)
            touched[int(gap["id"])] = gap
            todos.append(todo)

        return list(touched.values()), todos

    # ------------------------------------------------------------------ 导出

    def export_entries(self) -> dict[str, Any]:
        """导出清单：站点不重复，采集时段按（站点、采集时间）去重，只留第一条。"""
        self._ensure_bootstrapped()
        stations, total = self.list_entries(page=1, size=10000)

        readings = sorted(
            store.rows(READING_MODULE),
            key=lambda r: (str(r.get("站点编号", "")), _parse_time(r.get("采集时间")) or datetime.min),
        )
        seen: set[tuple[str, str]] = set()
        unique_readings: list[dict[str, Any]] = []
        for reading in readings:
            key = (str(reading.get("站点编号", "")).strip(), str(reading.get("采集时间", "")).strip())
            if key in seen:
                continue
            seen.add(key)
            unique_readings.append(self._reading_view(reading))

        gaps = self.list_gaps()
        return {
            "module": "environment",
            "total": total,
            "items": stations,
            "readings_total": len(unique_readings),
            "readings": unique_readings,
            "gaps": gaps,
        }
