# `codecross/cross_verification/triggers.py` 解析

## 文件作用
交叉验证触发规则，根据目标语言、执行错误、sanitizer 拦截和配置预算决定是否调用 ReproAgent。

- 源码行数：94
- 主要依赖：`re`、`typing`、`config`、`schemas`

## 函数与类

### `detect_cross_verification_trigger`（函数，第 47 行）
- 签名：`def detect_cross_verification_trigger(test_case: TestCase, runner_config: RunnerConfig, *, pytest_stderr: str | None=None, sanitizer_blocked: bool=False) -> CrossTrigger | None`
- 作用：根据 test_case 与运行上下文决定是否需要 LLM 交叉验证。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
