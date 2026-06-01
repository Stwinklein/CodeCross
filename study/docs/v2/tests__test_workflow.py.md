# `tests/test_workflow.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `workflow` 相关行为和回归场景。

- 源码行数：35
- 主要依赖：`pathlib`、`codecross`

## 函数与类

### `test_workflow_runs_v1_demo_with_two_reviewers`（函数，第 12 行）
- 签名：`def test_workflow_runs_v1_demo_with_two_reviewers()`
- 作用：V1 demo 应该产出两个 reviewer、失败测试证据和 high risk 结论。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
