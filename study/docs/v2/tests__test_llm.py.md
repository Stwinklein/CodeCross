# `tests/test_llm.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `llm` 相关行为和回归场景。

- 源码行数：64
- 主要依赖：`pytest`、`codecross`

## 函数与类

### `test_anthropic_uses_current_max_tokens_parameter`（函数，第 13 行）
- 签名：`def test_anthropic_uses_current_max_tokens_parameter(monkeypatch)`
- 作用：Anthropic provider 应使用 max_tokens，而不是旧字段 max_tokens_to_sample。

### `test_call_model_wraps_recoverable_runtime_error`（函数，第 36 行）
- 签名：`def test_call_model_wraps_recoverable_runtime_error(monkeypatch)`
- 作用：网络/超时类错误应包装成 ModelCallError，方便 agent 层 fallback。

### `test_call_model_does_not_wrap_local_programming_error`（函数，第 50 行）
- 签名：`def test_call_model_does_not_wrap_local_programming_error(monkeypatch)`
- 作用：非 provider/网络异常应暴露原始类型，避免代码 bug 被伪装成模型失败。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
