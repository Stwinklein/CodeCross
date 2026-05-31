from __future__ import annotations

"""CodeCross V0.1 单文件原型。

这个脚本保留了早期“Planner -> Reviewer -> Test Runner -> Board -> Evaluator”
的最小闭环，便于和正式的 codecross/ V1 拆分版做对照学习。
"""

import json
import operator
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

if os.getenv("DEEPSEEK_BASE_URL") and not os.getenv("DEEPSEEK_API_BASE"):
    os.environ["DEEPSEEK_API_BASE"] = os.environ["DEEPSEEK_BASE_URL"]

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


# =========================
# 1. Pydantic 输出结构
# =========================


class PlannerOutput(BaseModel):
    """Planner 的最小结构化输出。"""

    plan: str = Field(description="本次代码审查计划")


class TestCase(BaseModel):
    """V0.1 用函数调用模拟测试用例。"""

    name: str = Field(description="测试用例名称")
    function_name: str = Field(description="要测试的函数名")
    args: list[float] = Field(description="函数参数")
    expectation: Literal["returns", "should_not_raise"] = Field(
        description="returns 表示检查返回值；should_not_raise 表示不应该抛异常"
    )
    expected: float | None = Field(default=None, description="期望返回值")
    reason: str = Field(description="为什么需要这个测试")


class ReviewerOutput(BaseModel):
    """Reviewer 在 V0.1 中只输出测试用例。"""

    test_cases: list[TestCase] = Field(description="测试用例列表")


class EvaluatorOutput(BaseModel):
    """Evaluator 的最小风险报告。"""

    risk_level: Literal["low", "medium", "high"] = Field(description="风险等级")
    final_report: str = Field(description="最终报告")


class HighRiskReviewOutput(BaseModel):
    """高风险复核的最小输出。"""

    final_report: str = Field(description="高风险复核报告")


def json_output_rules(schema: type[BaseModel]) -> str:
    """把 Pydantic schema 转成提示词里的 JSON 输出约束。"""

    return f"""
输出要求：
- 只输出一个合法 JSON object，不要包 Markdown 代码块。
- JSON 必须严格符合下面的 JSON Schema，required 字段必须全部出现，不要替换字段名。
- 不要调用 tool、function_call 或 tool_call；本流程使用 DeepSeek JSON mode 解析结构化输出。

JSON Schema:
{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}
"""


# =========================
# 2. LangGraph State
# =========================


class CodeCrossState(TypedDict):
    """V0.1 LangGraph 状态。"""

    diff: str
    plan: NotRequired[str]

    # 后面多 reviewer 时，多个 reviewer 可以往这里追加。
    test_cases: Annotated[list[dict[str, Any]], operator.add]

    test_results: NotRequired[list[dict[str, Any]]]
    board_summary: NotRequired[str]
    risk_level: NotRequired[str]
    final_report: NotRequired[str]


# =========================
# 3. 被测试代码：V1 先用本地函数模拟
# =========================


def divide(a: float, b: float) -> float:
    """被测函数：故意不处理除零，用来演示测试发现风险。"""

    return a / b


FUNCTION_REGISTRY = {
    "divide": divide,
}


# =========================
# 4. 初始化 DeepSeek JSON mode 结构化模型
# =========================


llm = init_chat_model(
    model=DEEPSEEK_MODEL,
    model_provider="deepseek",
    temperature=0,
)


def make_structured_model(schema: type[BaseModel]):
    """给基础 LLM 包一层结构化输出解析。"""

    """使用 JSON mode 避开 create_agent(response_format=...) 的伪 tool-call。"""

    return llm.with_structured_output(schema, method="json_mode")


planner_model = make_structured_model(PlannerOutput)
reviewer_model = make_structured_model(ReviewerOutput)
evaluator_model = make_structured_model(EvaluatorOutput)
high_risk_review_model = make_structured_model(HighRiskReviewOutput)


# =========================
# 5. Prompts
# =========================


PLANNER_PROMPT = f"""
你是 CodeCross 的 Planner。
你需要根据单次 commit diff，制定最小审查计划。
只输出本次应该重点检查什么。

{json_output_rules(PlannerOutput)}
"""

REVIEWER_PROMPT = f"""
你是 CodeCross 的 Reviewer。
你的任务不是直接写报告，而是根据 diff 生成能暴露问题的测试用例。

当前 V1 只支持测试 divide(a, b) 这个函数。

你必须优先生成：
1. 正常路径测试
2. 边界条件测试
3. 可能触发异常的测试

{json_output_rules(ReviewerOutput)}
"""

EVALUATOR_PROMPT = f"""
你是 CodeCross 的 Evaluator。
你需要根据 Test Runner 的执行结果判断风险等级。

判断规则：
- 如果有测试失败，并且失败暴露潜在 bug，风险为 high
- 如果有轻微问题但不影响主要功能，风险为 medium
- 如果测试都通过且无明显问题，风险为 low

你需要输出 risk_level 和 final_report。

{json_output_rules(EvaluatorOutput)}
"""

HIGH_RISK_REVIEW_PROMPT = f"""
你是 High Risk Reviewer。
你只处理高风险问题。
你需要根据失败测试、错误信息和 diff，给出更明确的 bug 解释和修复建议。

{json_output_rules(HighRiskReviewOutput)}
"""


def invoke_structured(model, system_prompt: str, user_prompt: str):
    """统一调用结构化模型。"""

    return model.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )


# =========================
# 6. 定义节点
# =========================


def planner_node(state: CodeCrossState) -> dict[str, str]:
    """LangGraph 节点：生成审查计划。"""

    output: PlannerOutput = invoke_structured(
        planner_model,
        PLANNER_PROMPT,
        f"请为下面 commit diff 制定审查计划：\n\n{state['diff']}",
    )
    return {"plan": output.plan}


def reviewer_node(state: CodeCrossState) -> dict[str, list[dict[str, Any]]]:
    """LangGraph 节点：根据计划和 diff 生成测试用例。"""

    output: ReviewerOutput = invoke_structured(
        reviewer_model,
        REVIEWER_PROMPT,
        f"""
Review plan:
{state["plan"]}

Commit diff:
{state["diff"]}

请生成测试用例。
注意：当前只能测试 divide(a, b)。
""",
    )
    return {"test_cases": [case.model_dump() for case in output.test_cases]}


def execute_tests_node(state: CodeCrossState) -> dict[str, list[dict[str, Any]]]:
    """LangGraph 节点：直接调用本地函数模拟测试执行。"""

    results: list[dict[str, Any]] = []

    for case in state["test_cases"]:
        function_name = case["function_name"]
        args = case["args"]
        expectation = case["expectation"]
        expected = case.get("expected")

        func = FUNCTION_REGISTRY.get(function_name)
        if func is None:
            results.append(
                {
                    "test_name": case["name"],
                    "passed": False,
                    "error": f"Unknown function: {function_name}",
                    "case": case,
                }
            )
            continue

        try:
            actual = func(*args)
            if expectation == "returns":
                passed = actual == expected
                error = None if passed else f"Expected {expected}, got {actual}"
            elif expectation == "should_not_raise":
                passed = True
                error = None
            else:
                passed = False
                error = f"Unknown expectation: {expectation}"

            results.append(
                {
                    "test_name": case["name"],
                    "passed": passed,
                    "actual": actual,
                    "error": error,
                    "case": case,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "test_name": case["name"],
                    "passed": False,
                    "actual": None,
                    "error": repr(exc),
                    "case": case,
                }
            )

    return {"test_results": results}


def board_node(state: CodeCrossState) -> dict[str, str]:
    """LangGraph 节点：汇总测试结果，形成简化 Board。"""

    failed = [result for result in state["test_results"] if not result["passed"]]

    summary = f"""
CodeCross Board Summary

Total tests: {len(state["test_cases"])}
Failed tests: {len(failed)}

Failed details:
{json.dumps(failed, ensure_ascii=False, indent=2)}
"""
    return {"board_summary": summary}


def evaluator_node(state: CodeCrossState) -> dict[str, str]:
    """LangGraph 节点：根据 Board 判断风险等级。"""

    output: EvaluatorOutput = invoke_structured(
        evaluator_model,
        EVALUATOR_PROMPT,
        f"""
Commit diff:
{state["diff"]}

Review plan:
{state["plan"]}

Generated test cases:
{json.dumps(state["test_cases"], ensure_ascii=False, indent=2)}

Test runner results:
{json.dumps(state["test_results"], ensure_ascii=False, indent=2)}

Board summary:
{state["board_summary"]}

请判断风险等级并输出最终报告。
""",
    )

    return {
        "risk_level": output.risk_level,
        "final_report": output.final_report,
    }


def route_after_evaluator(state: CodeCrossState):
    """条件边：高风险时进入 high_risk_review。"""

    if state["risk_level"] == "high":
        return "high_risk_review"
    return END


def high_risk_review_node(state: CodeCrossState) -> dict[str, str]:
    """LangGraph 节点：高风险二次解释。"""

    output: HighRiskReviewOutput = invoke_structured(
        high_risk_review_model,
        HIGH_RISK_REVIEW_PROMPT,
        f"""
当前问题被 Evaluator 判断为 high risk。

Commit diff:
{state["diff"]}

Test results:
{json.dumps(state["test_results"], ensure_ascii=False, indent=2)}

Board summary:
{state["board_summary"]}

Evaluator report:
{state["final_report"]}

请给出更明确的高风险分析和修复建议。
""",
    )
    return {"final_report": output.final_report}


# =========================
# 7. 构建 LangGraph
# =========================


builder = StateGraph(CodeCrossState)

builder.add_node("planner", planner_node)
builder.add_node("reviewer", reviewer_node)
builder.add_node("test_runner", execute_tests_node)
builder.add_node("board", board_node)
builder.add_node("evaluator", evaluator_node)
builder.add_node("high_risk_review", high_risk_review_node)

builder.add_edge(START, "planner")
builder.add_edge("planner", "reviewer")
builder.add_edge("reviewer", "test_runner")
builder.add_edge("test_runner", "board")
builder.add_edge("board", "evaluator")
builder.add_conditional_edges(
    "evaluator",
    route_after_evaluator,
    {
        "high_risk_review": "high_risk_review",
        END: END,
    },
)
builder.add_edge("high_risk_review", END)

app = builder.compile()


# =========================
# 8. 测试运行
# =========================


test_diff = """
diff --git a/math_utils.py b/math_utils.py
+def divide(a, b):
+    return a / b
"""


def main() -> None:
    """脚本入口：运行内置 diff demo。"""

    result = app.invoke(
        {
            "diff": test_diff,
            "test_cases": [],
        }
    )

    print("====== FINAL RESULT ======")
    print("Risk Level:", result["risk_level"])
    print("\nFinal Report:")
    print(result["final_report"])


if __name__ == "__main__":
    main()
