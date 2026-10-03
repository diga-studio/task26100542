"""对账引擎：维护双方记录并回答对账查询。"""

from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Optional, Tuple, Union

from .model import (
    Record,
    ReconcileResult,
    Segment,
    SegmentKind,
    Side,
    SideView,
)

# 一侧在某原子区间上的有效取值：(job, rate)，无记录为 None
_Value = Optional[Tuple[str, int]]
# 合并键：分类 + 双方有效工种和单价（不含标识与修订号）
_Key = Tuple[SegmentKind, _Value, _Value]


class Reconciler:
    """单份服务协议的双边工时对账器（单线程、内存内）。"""

    def __init__(self) -> None:
        self._records: Dict[Side, Dict[str, Record]] = {
            Side.SERVICE: {},
            Side.CLIENT: {},
        }

    def put(self, side: Union[Side, str], record: Record) -> None:
        """新增一条报工记录。标识及同侧修订号唯一，记录不可修改。"""
        side = Side(side)
        if record.end <= record.start:
            raise ValueError("record.end must be greater than record.start")
        if record.rate < 0:
            raise ValueError("record.rate must be non-negative")
        bucket = self._records[side]
        if record.id in bucket:
            raise ValueError(f"duplicate record id: {record.id!r}")
        if any(r.revision == record.revision for r in bucket.values()):
            raise ValueError(f"duplicate revision on {side}: {record.revision}")
        bucket[record.id] = record

    def remove(self, side: Union[Side, str], record_id: str) -> None:
        """撤回该侧的一条记录。"""
        side = Side(side)
        try:
            del self._records[side][record_id]
        except KeyError:
            raise KeyError(f"no record {record_id!r} on {side}") from None

    def reconcile(self, start: int, end: int) -> ReconcileResult:
        """返回 [start, end) 内的对账片段及应付合计（整数分）。"""
        if end <= start:
            return ReconcileResult(segments=[], total_cents=0)

        service = self._effective_pieces(Side.SERVICE, start, end)
        client = self._effective_pieces(Side.CLIENT, start, end)

        segments: List[Segment] = []
        total = Fraction(0)
        # 按双方边界共同切出的原子区间逐个分类并合并
        points = sorted({p for piece in service + client for p in piece[:2]})
        prev_key: Optional[_Key] = None
        for lo, hi in zip(points, points[1:]):
            s_rec = _covering(service, lo)
            c_rec = _covering(client, lo)
            if s_rec is None and c_rec is None:
                prev_key = None
                continue
            kind = _classify(s_rec, c_rec)
            key = (kind, _value(s_rec), _value(c_rec))
            if segments and prev_key == key:
                segments[-1] = Segment(
                    start=segments[-1].start,
                    end=hi,
                    kind=segments[-1].kind,
                    service=segments[-1].service,
                    client=segments[-1].client,
                )
            else:
                segments.append(
                    Segment(
                        start=lo,
                        end=hi,
                        kind=kind,
                        service=_view(s_rec),
                        client=_view(c_rec),
                    )
                )
            prev_key = key
            if kind is SegmentKind.AGREED:
                total += Fraction((hi - lo) * s_rec.rate, 3600)

        # 只在最后对合计四舍五入到整数分
        return ReconcileResult(
            segments=segments, total_cents=int(total + Fraction(1, 2))
        )

    def _effective_pieces(
        self, side: Side, lo: int, hi: int
    ) -> List[Tuple[int, int, Optional[Record]]]:
        """把 [lo, hi) 切成原子区间，给出每个区间上该侧的有效记录。"""
        recs = [
            r
            for r in self._records[side].values()
            if r.start < hi and r.end > lo
        ]
        points = {lo, hi}
        for r in recs:
            points.add(max(r.start, lo))
            points.add(min(r.end, hi))
        ordered = sorted(points)
        pieces: List[Tuple[int, int, Optional[Record]]] = []
        for a, b in zip(ordered, ordered[1:]):
            best: Optional[Record] = None
            for r in recs:
                if r.start <= a and r.end >= b:
                    if best is None or r.revision > best.revision:
                        best = r
            pieces.append((a, b, best))
        return pieces


def _covering(
    pieces: List[Tuple[int, int, Optional[Record]]], at: int
) -> Optional[Record]:
    for lo, hi, rec in pieces:
        if lo <= at < hi:
            return rec
    return None


def _value(rec: Optional[Record]) -> _Value:
    return None if rec is None else (rec.job, rec.rate)


def _view(rec: Optional[Record]) -> Optional[SideView]:
    if rec is None:
        return None
    return SideView(id=rec.id, revision=rec.revision, job=rec.job, rate=rec.rate)


def _classify(
    service: Optional[Record], client: Optional[Record]
) -> SegmentKind:
    if service is None or client is None:
        return SegmentKind.ONE_SIDED
    if service.job == client.job and service.rate == client.rate:
        return SegmentKind.AGREED
    return SegmentKind.CONFLICT
