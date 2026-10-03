"""数据类型定义：双边工时对账库。"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Optional


class Side(str, enum.Enum):
    """提交记录的一方：服务方 / 用工方。"""

    SERVICE = "service"
    CLIENT = "client"


@dataclass(frozen=True)
class Record:
    """一条已确认的报工记录。

    时间为整数秒，区间左闭右开 [start, end)；rate 为每小时单价（非负整数分）。
    """

    id: str
    revision: int
    start: int
    end: int
    job: str
    rate: int


class SegmentKind(str, enum.Enum):
    """对账片段分类。"""

    AGREED = "agreed"        # 双方有效记录工种、单价一致
    CONFLICT = "conflict"    # 双方都有有效记录但值不同
    ONE_SIDED = "one_sided"  # 仅一方有有效记录


@dataclass(frozen=True)
class SideView:
    """某一片段内一侧的有效记录取值。"""

    id: str
    revision: int
    job: str
    rate: int


@dataclass(frozen=True)
class Segment:
    """对账结果片段，已裁剪到查询边界并按要求合并。"""

    start: int
    end: int
    kind: SegmentKind
    service: Optional[SideView]
    client: Optional[SideView]


@dataclass(frozen=True)
class ReconcileResult:
    """reconcile 的返回：片段列表 + 应付合计（整数分）。"""

    segments: List[Segment]
    total_cents: int
