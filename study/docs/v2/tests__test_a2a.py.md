# `tests/test_a2a.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `a2a` 相关行为和回归场景。

- 源码行数：56
- 主要依赖：`fastapi`、`a2aproject`、`codecross`

## 函数与类

### `test_a2a_agent_card_and_message_send`（函数，第 9 行）
- 签名：`def test_a2a_agent_card_and_message_send()`
- 作用：The demo A2A reviewer should expose discovery metadata and artifacts.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
