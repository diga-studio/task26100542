"""worklog_reconcile 的自动化测试。

1. 确定性场景：不同切分、交叉/嵌套修订、撤回后旧记录恢复、边界裁剪、
   有效值相同片段合并、不足一分片段只在合计时舍入。
2. 固定种子随机测试：乱序新增/撤回/查询，与逐秒朴素参考逐次核对完整结果。
"""

import random
import unittest
from fractions import Fraction

from worklog_reconcile import (
    Category,
    Reconciler,
    Record,
    SIDE_CLIENT,
    SIDE_PROVIDER,
)


def seg_view(seg):
    """片段的可比较视图：区间、分类、双方有效工种与单价。"""
    prov = (seg.provider.job_type, seg.provider.rate) if seg.provider else None
    cli = (seg.client.job_type, seg.client.rate) if seg.client else None
    return (seg.start, seg.end, seg.category, prov, cli)


def naive_effective(records, t):
    best = None
    for rec in records.values():
        if rec.start <= t < rec.end and (best is None or rec.revision > best.revision):
            best = rec
    return best


def naive_reconcile(provider_records, client_records, start, end):
    """逐秒扫描的朴素参考实现，返回 (片段视图列表, 合计分)。"""
    if end <= start:
        return [], 0
    views = []
    total = Fraction(0)
    for t in range(start, end):
        prov = naive_effective(provider_records, t)
        cli = naive_effective(client_records, t)
        if prov is None and cli is None:
            key = None
        else:
            if prov and cli:
                cat = (
                    Category.AGREED
                    if (prov.job_type, prov.rate) == (cli.job_type, cli.rate)
                    else Category.CONFLICT
                )
            else:
                cat = Category.ONE_SIDED
            key = (
                cat,
                (prov.job_type, prov.rate) if prov else None,
                (cli.job_type, cli.rate) if cli else None,
            )
            if cat is Category.AGREED:
                total += Fraction(prov.rate, 3600)
        if key is not None and views and views[-1][2:] == key and views[-1][1] == t:
            views[-1] = (views[-1][0], t + 1) + views[-1][2:]
        elif key is not None:
            views.append((t, t + 1) + key)
    return views, int(total + Fraction(1, 2))


class DeterministicTest(unittest.TestCase):
    def setUp(self):
        self.r = Reconciler()
        # 服务方：基础记录 + 嵌套修订（同值）+ 嵌套修订（不同值）
        self.r.put(SIDE_PROVIDER, Record("P1", 1, 0, 7200, "dev", 10000))
        self.r.put(SIDE_PROVIDER, Record("P2", 2, 1800, 3600, "dev", 10000))
        self.r.put(SIDE_PROVIDER, Record("P3", 3, 2700, 3000, "qa", 20000))
        # 用工方：完全不同的切分方式
        self.r.put(SIDE_CLIENT, Record("C1", 1, 900, 4500, "dev", 10000))
        self.r.put(SIDE_CLIENT, Record("C2", 2, 3600, 5400, "dev", 8000))

    def test_nested_revisions_clipping_and_merge(self):
        res = self.r.reconcile(1200, 4800)
        self.assertEqual(
            [seg_view(s) for s in res.segments],
            [
                # P1/P2 标识与修订号不同但有效值相同，必须合并
                (1200, 2700, Category.AGREED, ("dev", 10000), ("dev", 10000)),
                (2700, 3000, Category.CONFLICT, ("qa", 20000), ("dev", 10000)),
                (3000, 3600, Category.AGREED, ("dev", 10000), ("dev", 10000)),
                # C2 覆盖 [3600,5400)，4500 处 C1 消失但双方有效值不变，合并为一段
                (3600, 4800, Category.CONFLICT, ("dev", 10000), ("dev", 8000)),
            ],
        )
        # 一致工时 1500s + 600s = 2100s，2100*10000/3600 = 5833.33 -> 5833
        self.assertEqual(res.total_cents, 5833)

    def test_one_sided_tail(self):
        # C2 结束于 5400，之后仅剩服务方 P1：单边工时不计费
        res = self.r.reconcile(1200, 5600)
        self.assertEqual(
            seg_view(res.segments[-1]),
            (5400, 5600, Category.ONE_SIDED, ("dev", 10000), None),
        )
        self.assertEqual(res.total_cents, 5833)

    def test_remove_restores_older_record(self):
        self.r.remove(SIDE_PROVIDER, "P2")
        res = self.r.reconcile(1200, 4800)
        views = [seg_view(s) for s in res.segments]
        # P1 在 [1800,2700) 与 [3000,3600) 重新显现
        self.assertEqual(views[0], (1200, 2700, Category.AGREED, ("dev", 10000), ("dev", 10000)))
        self.assertEqual(res.segments[0].provider.id, "P1")
        self.assertEqual(views[2], (3000, 3600, Category.AGREED, ("dev", 10000), ("dev", 10000)))
        self.assertEqual(res.segments[2].provider.id, "P1")

        # 再撤回 P3：P1 全线恢复，[1200,3600) 合并为一段
        self.r.remove(SIDE_PROVIDER, "P3")
        res = self.r.reconcile(1200, 4800)
        self.assertEqual(
            [seg_view(s) for s in res.segments],
            [
                (1200, 3600, Category.AGREED, ("dev", 10000), ("dev", 10000)),
                (3600, 4800, Category.CONFLICT, ("dev", 10000), ("dev", 8000)),
            ],
        )
        self.assertEqual(res.total_cents, 6667)  # 2400*10000/3600 = 6666.67

    def test_revision_order_independent_of_arrival(self):
        # 乱序到达：低修订号后到达也不能覆盖高修订号
        r = Reconciler()
        r.put(SIDE_PROVIDER, Record("hi", 9, 100, 200, "dev", 100))
        r.put(SIDE_PROVIDER, Record("lo", 1, 0, 300, "qa", 100))
        r.put(SIDE_CLIENT, Record("c", 1, 0, 300, "dev", 100))
        res = r.reconcile(0, 300)
        self.assertEqual(
            [seg_view(s) for s in res.segments],
            [
                (0, 100, Category.CONFLICT, ("qa", 100), ("dev", 100)),
                (100, 200, Category.AGREED, ("dev", 100), ("dev", 100)),
                (200, 300, Category.CONFLICT, ("qa", 100), ("dev", 100)),
            ],
        )

    def test_sub_cent_fragments_rounded_only_at_total(self):
        r = Reconciler()
        # 两段一致工时各 1296s、单价 1 分/小时：各 0.36 分
        r.put(SIDE_PROVIDER, Record("pa", 1, 0, 1296, "x", 1))
        r.put(SIDE_PROVIDER, Record("pb", 1, 2000, 3296, "x", 1))
        r.put(SIDE_CLIENT, Record("ca", 1, 0, 1296, "x", 1))
        r.put(SIDE_CLIENT, Record("cb", 1, 2000, 3296, "x", 1))
        res = r.reconcile(0, 5000)
        agreed = [s for s in res.segments if s.category is Category.AGREED]
        self.assertEqual([(s.start, s.end) for s in agreed], [(0, 1296), (2000, 3296)])
        # 合计 0.72 分 -> 1；若逐段舍入则为 0
        self.assertEqual(res.total_cents, 1)

    def test_exact_half_cent_rounds_up(self):
        r = Reconciler()
        r.put(SIDE_PROVIDER, Record("p", 1, 0, 1800, "x", 1))
        r.put(SIDE_CLIENT, Record("c", 1, 0, 1800, "x", 1))
        self.assertEqual(r.reconcile(0, 1800).total_cents, 1)  # 0.5 -> 1

    def test_empty_and_invalid_ranges(self):
        r = Reconciler()
        self.assertEqual(r.reconcile(10, 10).segments, ())
        self.assertEqual(r.reconcile(20, 10).total_cents, 0)
        r.put(SIDE_PROVIDER, Record("p", 1, 0, 100, "x", 5))
        self.assertEqual(r.reconcile(200, 300).segments, ())


class RandomizedTest(unittest.TestCase):
    def test_against_naive_reference(self):
        rng = random.Random(20261005)
        rec = Reconciler()
        live = {SIDE_PROVIDER: {}, SIDE_CLIENT: {}}
        used_revisions = {SIDE_PROVIDER: set(), SIDE_CLIENT: set()}
        jobs = ["dev", "qa", "ops"]
        rates = [0, 1, 7, 8000, 10000, 12345]
        counter = 0

        def check_all():
            for _ in range(3):
                a = rng.randrange(0, 9000)
                b = a + rng.randrange(0, 900)
                start, end = min(a, b), max(a, b)
                got = rec.reconcile(start, end)
                want_views, want_total = naive_reconcile(
                    live[SIDE_PROVIDER], live[SIDE_CLIENT], start, end
                )
                self.assertEqual([seg_view(s) for s in got.segments], want_views)
                self.assertEqual(got.total_cents, want_total)

        for step in range(250):
            side = rng.choice([SIDE_PROVIDER, SIDE_CLIENT])
            action = rng.random()
            if action < 0.55 or not live[side]:
                # 新增：标识与同侧修订号唯一
                rev = rng.randrange(0, 10**6)
                while rev in used_revisions[side]:
                    rev = rng.randrange(0, 50)
                used_revisions[side].add(rev)
                start = rng.randrange(0, 8000)
                end = start + rng.randrange(1, 2000)
                record = Record(
                    id=f"{side}-{counter}",
                    revision=rev,
                    start=start,
                    end=end,
                    job_type=rng.choice(jobs),
                    rate=rng.choice(rates),
                )
                counter += 1
                rec.put(side, record)
                live[side][record.id] = record
            elif action < 0.8:
                # 撤回
                victim = rng.choice(list(live[side]))
                rec.remove(side, victim)
                del live[side][victim]
            check_all()


if __name__ == "__main__":
    unittest.main()
