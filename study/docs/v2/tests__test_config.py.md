# `tests/test_config.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `config` 相关行为和回归场景。

- 源码行数：28
- 主要依赖：`codecross`

## 函数与类

### `test_load_project_config_uses_two_env_channels`（函数，第 9 行）
- 签名：`def test_load_project_config_uses_two_env_channels(monkeypatch)`
- 作用：DeepSeek 和 Anthropic 环境变量应该生成两个启用 reviewer。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
