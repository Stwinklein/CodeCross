# `tests/test_cross_verification_e2e.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `cross / verification / e2e` 相关行为和回归场景。

- 源码行数：117
- 主要依赖：`os`、`re`、`pytest`、`codecross`

## 函数与类

### `test_repro_agent_real_model_handles_unsupported_language`（函数，第 41 行）
- 签名：`def test_repro_agent_real_model_handles_unsupported_language()`
- 作用：跑一次真实 ReproAgent，要求它对一个 JavaScript finding 给出复现思路。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
