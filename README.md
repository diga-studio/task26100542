# task26100542

## 双边工时对账库

纯 Python 标准库实现，单协议、单线程、内存内运行，无第三方依赖。

### 文件

- `worklog_reconcile.py` — 库：`Reconciler.put / remove / reconcile`
- `test_worklog_reconcile.py` — 自动化测试（确定性场景 + 固定种子随机对照朴素参考）

### 使用

```python
from worklog_reconcile import Reconciler, Record, SIDE_PROVIDER, SIDE_CLIENT

r = Reconciler()
r.put(SIDE_PROVIDER, Record("p1", revision=1, start=0, end=3600,
                            job_type="dev", rate=10000))  # 100 元/小时
r.put(SIDE_CLIENT, Record("c1", revision=1, start=1800, end=5400,
                          job_type="dev", rate=10000))
r.remove(SIDE_PROVIDER, "p1")          # 撤回
result = r.reconcile(0, 7200)          # -> ReconcileResult(segments, total_cents)
```

- 时间：整数秒，区间左闭右开；`rate`：非负整数分/小时。
- 同侧重叠时取覆盖该时刻、未撤回记录中修订号最大者；撤回后旧记录自动恢复。
- 片段分类：`agreed`（一致，计费）/ `conflict`（差异）/ `one_sided`（单边）。
- 相邻且分类、双方有效工种与单价相同的片段自动合并（标识/修订号不同不阻止合并）。
- 应付合计按 `Fraction` 精确累加，最后一次性四舍五入到整数分。

### 运行测试

```bash
python3 -m unittest test_worklog_reconcile -v
```
