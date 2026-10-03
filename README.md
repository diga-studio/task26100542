# 双边工时对账库

纯 Python 标准库实现，单份服务协议、单线程、内存内运行。

## 概念

- 服务方（`Side.SERVICE`）与用工方（`Side.CLIENT`）各自提交已确认的报工记录。
- 记录不可修改；同一侧记录可任意重叠，每一时刻以覆盖该时刻且未撤回记录中
  修订号最大者为有效记录；高修订号只覆盖自身区间。
- 撤回有效记录后，其下未撤回的记录重新显现。
- 片段分类：`AGREED`（双方有效且工种、单价一致，计入应付）、
  `CONFLICT`（双方都有记录但值不同）、`ONE_SIDED`（仅一方有记录）。
- 应付合计用分数精确累加所有一致工时，只在最后四舍五入到整数分。

## 用法

```python
from recon import Reconciler, Record, Side

r = Reconciler()
r.put(Side.SERVICE, Record(id="s1", revision=1, start=0, end=3600,
                           job="dev", rate=3600))   # 3600 分/小时
r.put(Side.CLIENT,  Record(id="c1", revision=1, start=0, end=3600,
                           job="dev", rate=3600))
r.remove(Side.SERVICE, "s1")                        # 撤回

result = r.reconcile(0, 7200)
for seg in result.segments:
    print(seg.start, seg.end, seg.kind, seg.service, seg.client)
print(result.total_cents)                           # 应付合计（整数分）
```

`Record` 字段：`id`、`revision`、`start`/`end`（整数秒，左闭右开）、
`job`（工种）、`rate`（每小时单价，非负整数分）。

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

- `tests/test_scenarios.py`：不同切分合并、交叉/嵌套修订、撤回恢复、
  查询边界裁剪、不足一分片段只在合计时舍入、到达顺序无关性。
- `tests/test_fuzz.py`：固定随机种子的乱序新增/撤回/查询，
  与逐秒朴素参考计算逐次核对全部片段与合计。
