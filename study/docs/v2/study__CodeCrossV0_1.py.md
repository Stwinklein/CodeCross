# `study/CodeCrossV0_1.py` 解析

## 文件作用
最早期 V0.1 原型，用内置除法 demo 展示结构化 LLM、测试执行、Board 和风险评估的最小闭环。

- 源码行数：452
- 主要依赖：`json`、`operator`、`os`、`pathlib`、`typing`、`dotenv`、`langchain`、`langchain_core`、`pydantic`、`typing_extensions`、`langgraph`

## 函数与类

### `PlannerOutput`（类，第 38 行）
- 签名：`class PlannerOutput(BaseModel)`
- 作用：Planner 的最小结构化输出。
- 主要字段/属性：
  - `plan: str = Field(description='本次代码审查计划')`

### `TestCase`（类，第 44 行）
- 签名：`class TestCase(BaseModel)`
- 作用：V0.1 用函数调用模拟测试用例。
- 主要字段/属性：
  - `name: str = Field(description='测试用例名称')`
  - `function_name: str = Field(description='要测试的函数名')`
  - `args: list[float] = Field(description='函数参数')`
  - `expectation: Literal['returns', 'should_not_raise'] = Field(description='returns 表示检查返回值；should_not_raise 表示不应该抛异常')`
  - `expected: float | None = Field(default=None, description='期望返回值')`
  - `reason: str = Field(description='为什么需要这个测试')`

### `ReviewerOutput`（类，第 57 行）
- 签名：`class ReviewerOutput(BaseModel)`
- 作用：Reviewer 在 V0.1 中只输出测试用例。
- 主要字段/属性：
  - `test_cases: list[TestCase] = Field(description='测试用例列表')`

### `EvaluatorOutput`（类，第 63 行）
- 签名：`class EvaluatorOutput(BaseModel)`
- 作用：Evaluator 的最小风险报告。
- 主要字段/属性：
  - `risk_level: Literal['low', 'medium', 'high'] = Field(description='风险等级')`
  - `final_report: str = Field(description='最终报告')`

### `HighRiskReviewOutput`（类，第 70 行）
- 签名：`class HighRiskReviewOutput(BaseModel)`
- 作用：高风险复核的最小输出。
- 主要字段/属性：
  - `final_report: str = Field(description='高风险复核报告')`

### `json_output_rules`（函数，第 76 行）
- 签名：`def json_output_rules(schema: type[BaseModel]) -> str`
- 作用：把 Pydantic schema 转成提示词里的 JSON 输出约束。

### `CodeCrossState`（类，第 95 行）
- 签名：`class CodeCrossState(TypedDict)`
- 作用：V0.1 LangGraph 状态。
- 主要字段/属性：
  - `diff: str`
  - `plan: NotRequired[str]`
  - `test_cases: Annotated[list[dict[str, Any]], operator.add]`
  - `test_results: NotRequired[list[dict[str, Any]]]`
  - `board_summary: NotRequired[str]`
  - `risk_level: NotRequired[str]`
  - `final_report: NotRequired[str]`

### `divide`（函数，第 115 行）
- 签名：`def divide(a: float, b: float) -> float`
- 作用：被测函数：故意不处理除零，用来演示测试发现风险。

### `make_structured_model`（函数，第 138 行）
- 签名：`def make_structured_model(schema: type[BaseModel])`
- 作用：给基础 LLM 包一层结构化输出解析。

### `invoke_structured`（函数，第 202 行）
- 签名：`def invoke_structured(model, system_prompt: str, user_prompt: str)`
- 作用：统一调用结构化模型。

### `planner_node`（函数，第 218 行）
- 签名：`def planner_node(state: CodeCrossState) -> dict[str, str]`
- 作用：LangGraph 节点：生成审查计划。

### `reviewer_node`（函数，第 229 行）
- 签名：`def reviewer_node(state: CodeCrossState) -> dict[str, list[dict[str, Any]]]`
- 作用：LangGraph 节点：根据计划和 diff 生成测试用例。

### `execute_tests_node`（函数，第 249 行）
- 签名：`def execute_tests_node(state: CodeCrossState) -> dict[str, list[dict[str, Any]]]`
- 作用：LangGraph 节点：直接调用本地函数模拟测试执行。

### `board_node`（函数，第 307 行）
- 签名：`def board_node(state: CodeCrossState) -> dict[str, str]`
- 作用：LangGraph 节点：汇总测试结果，形成简化 Board。

### `evaluator_node`（函数，第 324 行）
- 签名：`def evaluator_node(state: CodeCrossState) -> dict[str, str]`
- 作用：LangGraph 节点：根据 Board 判断风险等级。

### `route_after_evaluator`（函数，第 356 行）
- 签名：`def route_after_evaluator(state: CodeCrossState)`
- 作用：条件边：高风险时进入 high_risk_review。

### `high_risk_review_node`（函数，第 364 行）
- 签名：`def high_risk_review_node(state: CodeCrossState) -> dict[str, str]`
- 作用：LangGraph 节点：高风险二次解释。

### `main`（函数，第 435 行）
- 签名：`def main() -> None`
- 作用：脚本入口：运行内置 diff demo。

## 学习提示
这是学习/原型代码，重点看它如何用更小的例子解释正式实现里的设计。
