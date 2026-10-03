"""手工构造场景：不同切分、交叉/嵌套修订、撤回恢复、边界裁剪、合并、舍入。"""

import unittest

from recon import Reconciler, Record, SegmentKind, Side

S = Side.SERVICE
C = Side.CLIENT


def rec(rid, rev, start, end, job="dev", rate=3600):
    return Record(id=rid, revision=rev, start=start, end=end, job=job, rate=rate)


def summary(result):
    return [(s.start, s.end, s.kind) for s in result.segments]


class SplitAndMergeTest(unittest.TestCase):
    def test_different_splits_merge_into_one_segment(self):
        r = Reconciler()
        r.put(S, rec("s1", 1, 0, 100))
        # 用工方用另一种切分方式，标识与修订号不同不应阻止合并
        r.put(C, rec("c1", 1, 0, 30))
        r.put(C, rec("c2", 2, 30, 70))
        r.put(C, rec("c3", 3, 70, 100))
        res = r.reconcile(0, 100)
        self.assertEqual(summary(res), [(0, 100, SegmentKind.AGREED)])
        self.assertEqual(res.total_cents, 100)  # 100s * 3600分/3600s

    def test_query_boundary_clipping(self):
        r = Reconciler()
        r.put(S, rec("s1", 1, 0, 100))
        r.put(C, rec("c1", 1, 0, 100))
        res = r.reconcile(10, 90)
        self.assertEqual(summary(res), [(10, 90, SegmentKind.AGREED)])
        self.assertEqual(res.total_cents, 80)
        # 完全落在记录之外的查询没有片段
        self.assertEqual(r.reconcile(200, 300).segments, [])
        self.assertEqual(r.reconcile(200, 300).total_cents, 0)


class RevisionOverlayTest(unittest.TestCase):
    def setUp(self):
        self.r = Reconciler()
        # 高修订号先到达：到达顺序不影响有效覆盖
        self.r.put(S, rec("s_high", 9, 20, 40, rate=7200))
        self.r.put(S, rec("s_base", 1, 0, 100))
        # 嵌套修订：交叉 + 被包含
        self.r.put(C, rec("c_base", 1, 0, 100))
        self.r.put(C, rec("c_mid", 5, 30, 60, job="ops"))
        self.r.put(C, rec("c_inner", 7, 35, 45, rate=9000))

    def test_nested_overlay_classification(self):
        res = self.r.reconcile(0, 100)
        self.assertEqual(
            summary(res),
            [
                (0, 20, SegmentKind.AGREED),      # s_base vs c_base
                (20, 30, SegmentKind.CONFLICT),   # s_high(7200) vs c_base(3600)
                (30, 35, SegmentKind.CONFLICT),   # s_high(dev) vs c_mid(ops)
                (35, 40, SegmentKind.CONFLICT),   # s_high(7200) vs c_inner(9000)
                (40, 45, SegmentKind.CONFLICT),   # s_base(3600) vs c_inner(9000)
                (45, 60, SegmentKind.CONFLICT),   # s_base(dev) vs c_mid(ops)
                (60, 100, SegmentKind.AGREED),    # s_base vs c_base
            ],
        )
        # 一致工时 20s + 40s = 60s，每秒 1 分
        self.assertEqual(res.total_cents, 60)

    def test_high_revision_does_not_invalidate_rest_of_old_record(self):
        # s_high 只覆盖 [20,40)，s_base 的其余部分仍然有效
        res = self.r.reconcile(0, 20)
        self.assertEqual(summary(res), [(0, 20, SegmentKind.AGREED)])
        res = self.r.reconcile(40, 45)
        seg = res.segments[0]
        self.assertEqual(seg.service.id, "s_base")

    def test_remove_reveals_underlying_record(self):
        self.r.remove(S, "s_high")
        res = self.r.reconcile(0, 100)
        # s_base 重新显现；c_inner/c_mid 仍造成差异
        self.assertEqual(
            summary(res),
            [
                (0, 30, SegmentKind.AGREED),
                (30, 35, SegmentKind.CONFLICT),
                (35, 45, SegmentKind.CONFLICT),
                (45, 60, SegmentKind.CONFLICT),
                (60, 100, SegmentKind.AGREED),
            ],
        )
        self.assertEqual(res.total_cents, 70)
        # 撤回嵌套修订后，底层记录依次显现
        self.r.remove(C, "c_inner")
        self.r.remove(C, "c_mid")
        res = self.r.reconcile(0, 100)
        self.assertEqual(summary(res), [(0, 100, SegmentKind.AGREED)])
        self.assertEqual(res.total_cents, 100)


class OneSidedTest(unittest.TestCase):
    def test_one_sided_segments_not_payable(self):
        r = Reconciler()
        r.put(S, rec("s1", 1, 0, 100))
        r.put(C, rec("c1", 1, 40, 60))
        res = r.reconcile(0, 100)
        self.assertEqual(
            summary(res),
            [
                (0, 40, SegmentKind.ONE_SIDED),
                (40, 60, SegmentKind.AGREED),
                (60, 100, SegmentKind.ONE_SIDED),
            ],
        )
        self.assertEqual(res.total_cents, 20)
        seg = res.segments[0]
        self.assertIsNotNone(seg.service)
        self.assertIsNone(seg.client)


class RoundingTest(unittest.TestCase):
    def test_rounding_only_at_total(self):
        r = Reconciler()
        # 单价 1 分/小时，两段各 1800 秒 = 各 0.5 分
        r.put(S, rec("s1", 1, 0, 1800, rate=1))
        r.put(C, rec("c1", 1, 0, 1800, rate=1))
        r.put(S, rec("s2", 2, 3600, 5400, rate=1))
        r.put(C, rec("c2", 2, 3600, 5400, rate=1))
        res = r.reconcile(0, 5400)
        kinds = [k for _, _, k in summary(res)]
        self.assertEqual(kinds, [SegmentKind.AGREED, SegmentKind.AGREED])
        # 若逐段舍入则 0+0=0；只在合计时舍入得到 1
        self.assertEqual(res.total_cents, 1)

    def test_exact_total_no_rounding_error(self):
        r = Reconciler()
        # 三段各 1200 秒、1 分/小时 => 合计恰好 1 分
        for i in range(3):
            r.put(S, rec(f"s{i}", i + 1, i * 1200, (i + 1) * 1200, rate=1))
            r.put(C, rec(f"c{i}", i + 1, i * 1200, (i + 1) * 1200, rate=1))
        res = r.reconcile(0, 3600)
        self.assertEqual(res.total_cents, 1)


class OrderIndependenceTest(unittest.TestCase):
    def test_arrival_order_does_not_change_result(self):
        records = [
            (S, rec("s1", 1, 0, 100)),
            (S, rec("s2", 5, 10, 50, rate=7200)),
            (C, rec("c1", 1, 0, 60)),
            (C, rec("c2", 3, 40, 100, rate=7200)),
        ]
        results = []
        for order in ([0, 1, 2, 3], [3, 2, 1, 0], [1, 3, 0, 2]):
            r = Reconciler()
            for i in order:
                side, record = records[i]
                r.put(side, record)
            res = r.reconcile(0, 100)
            results.append((summary(res), res.total_cents))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
