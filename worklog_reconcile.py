"""双边工时对账库（单协议、单线程、内存内）。

服务方与用工方各自提交报工记录，reconcile 对指定区间给出对账片段与应付合计。

语义约定：
- 区间均为整数秒、左闭右开；单价为非负整数分/小时。
- 同一侧记录可任意重叠；每一时刻取覆盖该时刻且未撤回记录中修订号最大者为有效记录。
- 双方有效记录的工种与单价均相同为一致工时（计费）；双方都有但值不同为差异工时；
  仅一方有记录为单边工时，后两者不计费。
- 相邻且分类、双方有效工种与单价完全相同的片段合并（标识/修订号不同不阻止合并）。
- 应付合计对所有一致工时按 Fraction 精确累加，最后一次性四舍五入到整数分。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

SIDE_PROVIDER = "provider"  # 服务方
SIDE_CLIENT = "client"      # 用工方

_SIDES = (SIDE_PROVIDER, SIDE_CLIENT)
_SECONDS_PER_HOUR = 3600


class Category(Enum):
    """片段分类。"""

    AGREED = "agreed"        # 双方一致工时（计费）
    CONFLICT = "conflict"    # 双方都有记录但工种或单价不同
    ONE_SIDED = "one_sided"  # 仅一方有记录


@dataclass(frozen=True)
class Record:
    """一条已确认的报工记录。创建后不可修改。"""

    id: str
    revision: int
    start: int  # 整数秒，左闭
    end: int    # 整数秒，右开
    job_type: str
    rate: int   # 每小时单价，非负整数分


@dataclass(frozen=True)
class Segment:
    """对账片段，区间左闭右开，已裁剪到查询边界。"""

    start: int
    end: int
    category: Category
    provider: Optional[Record]  # 服务方有效记录（无则 None）
    client: Optional[Record]    # 用工方有效记录（无则 None）


@dataclass(frozen=True)
class ReconcileResult:
    segments: Tuple[Segment, ...]
    total_cents: int  # 应付合计，整数分（最后一次性四舍五入）


def _effective_at(records: Dict[str, Record], moment: int) -> Optional[Record]:
    """返回覆盖 moment 的未撤回记录中修订号最大者。"""
    best: Optional[Record] = None
    for rec in records.values():
        if rec.start <= moment < rec.end:
            if best is None or rec.revision > best.revision:
                best = rec
    return best


def _classify(provider: Optional[Record], client: Optional[Record]) -> Category:
    if provider is not None and client is not None:
        if provider.job_type == client.job_type and provider.rate == client.rate:
            return Category.AGREED
        return Category.CONFLICT
    return Category.ONE_SIDED


def _merge_key(seg: Segment) -> Tuple:
    """合并键：分类 + 双方有效工种与单价（不含标识与修订号）。"""
    prov = (seg.provider.job_type, seg.provider.rate) if seg.provider else None
    cli = (seg.client.job_type, seg.client.rate) if seg.client else None
    return (seg.category, prov, cli)


class Reconciler:
    """单份服务协议的对账器。"""

    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, Record]] = {
            SIDE_PROVIDER: {},
            SIDE_CLIENT: {},
        }

    def put(self, side: str, record: Record) -> None:
        """新增一条报工记录。输入保证标识及同侧修订号唯一。"""
        if side not in _SIDES:
            raise ValueError(f"unknown side: {side!r}")
        if record.id in self._records[side]:
            raise ValueError(f"duplicate record id on {side}: {record.id!r}")
        if record.end <= record.start:
            raise ValueError("record end must be greater than start")
        if record.rate < 0:
            raise ValueError("rate must be non-negative")
        self._records[side][record.id] = record

    def remove(self, side: str, record_id: str) -> None:
        """撤回该侧的一条记录。"""
        if side not in _SIDES:
            raise ValueError(f"unknown side: {side!r}")
        try:
            del self._records[side][record_id]
        except KeyError:
            raise KeyError(f"no record {record_id!r} on {side}") from None

    def reconcile(self, start: int, end: int) -> ReconcileResult:
        """返回 [start, end) 内的对账片段及应付合计。"""
        if end <= start:
            return ReconcileResult(segments=(), total_cents=0)

        prov_records = self._records[SIDE_PROVIDER]
        cli_records = self._records[SIDE_CLIENT]

        # 收集所有可能改变有效覆盖的边界点，并裁剪到查询区间。
        cuts = {start, end}
        for rec in list(prov_records.values()) + list(cli_records.values()):
            lo = max(rec.start, start)
            hi = min(rec.end, end)
            if lo < hi:
                cuts.add(lo)
                cuts.add(hi)
        points = sorted(cuts)

        merged: List[Segment] = []
        total = Fraction(0)
        for lo, hi in zip(points, points[1:]):
            prov = _effective_at(prov_records, lo)
            cli = _effective_at(cli_records, lo)
            if prov is None and cli is None:
                continue
            seg = Segment(
                start=lo,
                end=hi,
                category=_classify(prov, cli),
                provider=prov,
                client=cli,
            )
            if merged and merged[-1].end == seg.start and _merge_key(merged[-1]) == _merge_key(seg):
                prev = merged[-1]
                merged[-1] = Segment(
                    start=prev.start,
                    end=seg.end,
                    category=prev.category,
                    provider=prev.provider,
                    client=prev.client,
                )
            else:
                merged.append(seg)

        for seg in merged:
            if seg.category is Category.AGREED:
                total += Fraction(seg.end - seg.start) * seg.provider.rate / _SECONDS_PER_HOUR

        return ReconcileResult(
            segments=tuple(merged),
            total_cents=_round_half_up(total),
        )


def _round_half_up(value: Fraction) -> int:
    """非负金额四舍五入到整数分。"""
    return int(value + Fraction(1, 2))
