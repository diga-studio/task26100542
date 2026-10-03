"""固定随机种子的乱序新增/撤回/查询，与逐秒朴素参考计算逐次核对。"""

import random
import unittest
from fractions import Fraction

from recon import Reconciler, Record, Segment, SegmentKind, Side, SideView

SIDES = (Side.SERVICE, Side.CLIENT)


def effective_at(records, t):
    """t 时刻的有效记录：覆盖 t 且未撤回记录中修订号最大者。"""
    best = None
    for r in records.values():
        if r.start <= t < r.end:
            if best is None or r.revision > best.revision:
                best = r
    return best


def reference_reconcile(records, start, end):
    """逐秒朴素参考：直接按规格定义计算，不做任何增量优化。"""
    segments = []
    total = Fraction(0)
    prev_key = None
    for t in range(start, end):
        s = effective_at(records[Side.SERVICE], t)
        c = effective_at(records[Side.CLIENT], t)
        if s is None and c is None:
            prev_key = None
            continue
        if s is None or c is None:
            kind = SegmentKind.ONE_SIDED
        elif s.job == c.job and s.rate == c.rate:
            kind = SegmentKind.AGREED
        else:
            kind = SegmentKind.CONFLICT
        key = (
            kind,
            None if s is None else (s.job, s.rate),
            None if c is None else (c.job, c.rate),
        )
        if segments and prev_key == key:
            segments[-1][1] = t + 1
        else:
            segments.append([t, t + 1, kind, s, c])
        prev_key = key
        if kind is SegmentKind.AGREED:
            total += Fraction(s.rate, 3600)
    out = [
        Segment(
            start=lo,
            end=hi,
            kind=kind,
            service=None if s is None else SideView(s.id, s.revision, s.job, s.rate),
            client=None if c is None else SideView(c.id, c.revision, c.job, c.rate),
        )
        for lo, hi, kind, s, c in segments
    ]
    return out, int(total + Fraction(1, 2))


class FuzzTest(unittest.TestCase):
    def test_against_naive_reference(self):
        rng = random.Random(20261005)
        engine = Reconciler()
        records = {Side.SERVICE: {}, Side.CLIENT: {}}
        used_revisions = {Side.SERVICE: set(), Side.CLIENT: set()}
        next_id = {Side.SERVICE: 0, Side.CLIENT: 0}
        jobs = ["dev", "ops", "qa"]
        rates = [0, 1, 7, 100, 3600, 7200]
        horizon = 180

        def check(start, end):
            expected_segments, expected_total = reference_reconcile(
                records, start, end
            )
            result = engine.reconcile(start, end)
            self.assertEqual(
                result.segments,
                expected_segments,
                msg=f"segments differ on [{start}, {end})",
            )
            self.assertEqual(
                result.total_cents,
                expected_total,
                msg=f"total differs on [{start}, {end})",
            )

        for step in range(400):
            action = rng.random()
            side = rng.choice(SIDES)
            if action < 0.55 or not records[side]:
                # 新增：标识与同侧修订号唯一
                rid = f"{side.value}-{next_id[side]}"
                next_id[side] += 1
                rev = rng.randrange(0, 10_000)
                while rev in used_revisions[side]:
                    rev = rng.randrange(0, 10_000)
                used_revisions[side].add(rev)
                start = rng.randrange(0, horizon)
                length = rng.randrange(1, 60)
                record = Record(
                    id=rid,
                    revision=rev,
                    start=start,
                    end=start + length,
                    job=rng.choice(jobs),
                    rate=rng.choice(rates),
                )
                engine.put(side, record)
                records[side][rid] = record
            elif action < 0.8:
                # 撤回
                rid = rng.choice(list(records[side]))
                engine.remove(side, rid)
                del records[side][rid]
            # 每次操作后都核对：随机区间 + 全覆盖区间
            q_start = rng.randrange(0, horizon)
            q_end = rng.randrange(q_start, horizon + 30)
            check(q_start, q_end)
            check(0, horizon + 60)

        # 全部撤回后必须为空
        for side in SIDES:
            for rid in list(records[side]):
                engine.remove(side, rid)
                del records[side][rid]
        check(0, horizon + 60)


if __name__ == "__main__":
    unittest.main()
