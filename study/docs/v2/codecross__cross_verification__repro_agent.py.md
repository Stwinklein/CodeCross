# `codecross/cross_verification/repro_agent.py` 解析

## 文件作用
ReproAgent 实现，在 pytest 不适用、环境异常或 sanitizer 拦截时，用 LLM 对 finding 可复现性做结构化交叉验证。

- 源码行数：278
- 主要依赖：`time`、`typing`、`pydantic`、`config`、`llm`、`schemas`

## 函数与类

### `ReproSpec`（类，第 41 行）
- 签名：`class ReproSpec(BaseModel)`
- 作用：ReproAgent 的强类型回包。
- 主要字段/属性：
  - `status: Literal['confirmed', 'supported', 'refuted', 'inconclusive', 'abstain']`
  - `reasoning: str = Field(default='', description='≤600 字符的中文判断理由')`
  - `reproduction_steps: list[str] = Field(default_factory=list)`
  - `expected_observation: str = ''`
  - `counter_examples: list[str] = Field(default_factory=list)`
  - `abstain_reason: str = ''`

### `_strength_from_status`（函数，第 69 行）
- 签名：`def _strength_from_status(status: str) -> EvidenceStrength`
- 作用：把 ReproAgent 的 status 翻译成 board 用的证据强度。

### `_truncate`（函数，第 83 行）
- 签名：`def _truncate(text: str, limit: int) -> str`
- 作用：轻量截断，防止 prompt 被超长上下文撑爆。

### `_build_user_prompt`（函数，第 95 行）
- 签名：`def _build_user_prompt(*, finding: Finding, test_case: TestCase, code_context: CodeContext | None, trigger: str | None) -> str`
- 作用：组装给 ReproAgent 的用户消息。

### `run_repro_agent`（函数，第 133 行）
- 签名：`def run_repro_agent(*, finding: Finding, test_case: TestCase, code_context: CodeContext | None, config: ProjectConfig, trigger: str | None) -> CrossVerificationPayload`
- 作用：执行 ReproAgent 一次调用并返回 payload。

### `_spec_to_payload`（函数，第 204 行）
- 签名：`def _spec_to_payload(spec: ReproSpec, *, settings: ModelSettings, duration_ms: int=0) -> CrossVerificationPayload`
- 作用：把 LLM 的强类型回包翻译成 board 用的 payload。

### `_inconclusive_payload`（函数，第 232 行）
- 签名：`def _inconclusive_payload(*, model: str, reason: str) -> CrossVerificationPayload`
- 作用：统一构造“失败 / 缺配置”这种 inconclusive 路径的 payload。

### `_mock_payload`（函数，第 245 行）
- 签名：`def _mock_payload(*, settings: ModelSettings, finding: Finding, trigger: str | None) -> CrossVerificationPayload`
- 作用：mock_llm=True 时的 deterministic 输出。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
