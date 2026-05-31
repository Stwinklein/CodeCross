"""CodeCross 最小雏形：用 LangGraph 编排两个 Code Review Agent。

运行示例：

    python study/CodeCrossMiniLangGraph.py
    python study/CodeCrossMiniLangGraph.py --file study/SoloAgent.py

这个文件刻意写成“单文件学习版”，方便你先看懂多 Agent 的数据流。
后面真正做项目时，再把 prompt、agent、graph、CLI 拆成多个模块。
正式 V1 已经拆到 codecross/，这个文件继续作为学习对照。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph


# 当前文件在 study/ 目录下，parents[1] 就是项目根目录 D:\HaloWorkspace\CodeCross。
# 注意：.env 文件不会被 Python 自动加载，必须显式调用 load_dotenv。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


# langchain-deepseek 读取的 base url 变量名是 DEEPSEEK_API_BASE。
# 你现在 .env 里用的是 DEEPSEEK_BASE_URL，所以这里做一次兼容映射。
if os.getenv("DEEPSEEK_BASE_URL") and not os.getenv("DEEPSEEK_API_BASE"):
    os.environ["DEEPSEEK_API_BASE"] = os.environ["DEEPSEEK_BASE_URL"]


# 模型名也放成环境变量可覆盖，避免以后模型升级时改代码。
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")


class CodeCrossState(TypedDict, total=False):
    """LangGraph 在节点之间传递的状态。

    你可以把 State 理解成一张“流程单”：
    - 开始时只有待审查代码；
    - 每个 agent 节点往里面补自己的审查结果；
    - 最后 aggregator 节点读取两个结果并生成最终报告。
    """

    task: str
    file_path: str
    code: str
    numbered_code: str
    deepseek_review: str
    anthropic_review: str
    final_report: str


DEMO_CODE = """\
import subprocess

API_TOKEN = "demo-token-please-do-not-commit"


def add_item(value, cache=[]):
    cache.append(value)
    print("debug", cache)
    return cache


def run(user_input):
    return subprocess.run(user_input, shell=True)
"""


DEEPSEEK_REVIEWER_PROMPT = """\
你是 CodeCross 里的 DeepSeek Reviewer Agent。

你的审查重点：
1. 运行时 bug；
2. 可维护性问题；
3. Python 常见陷阱；
4. 代码是否容易在生产环境中出错。

输出要求：
- 使用 Markdown；
- 只报告你有把握的问题；
- 每条问题尽量包含：文件、行号、严重程度、问题说明、修改建议；
- 不要重写整份代码；
- 如果没有发现问题，明确说“未发现明显问题”。
"""


ANTHROPIC_REVIEWER_PROMPT = """\
你是 CodeCross 里的 Anthropic Reviewer Agent。

你的审查重点：
1. 安全风险；
2. 命令注入、密钥泄露、危险 API；
3. 边界条件和失败场景；
4. 需要人工确认的不确定点。

输出要求：
- 使用 Markdown；
- 只报告你有把握的问题；
- 每条问题尽量包含：文件、行号、严重程度、问题说明、修改建议；
- 不要重写整份代码；
- 如果没有发现问题，明确说“未发现明显问题”。
"""


def require_env(name: str) -> None:
    """提前检查环境变量，报错时比 SDK 自己的异常更好懂。"""

    if not os.getenv(name):
        raise RuntimeError(
            f"缺少环境变量 {name}。请确认项目根目录 .env 中已经配置，并且脚本加载路径正确。"
        )


def content_to_text(content: Any) -> str:
    """把 LangChain message.content 统一转成字符串。

    大多数模型会返回 str；少数模型可能返回分段 content list。
    这里做兼容，避免不同 provider 的返回结构影响后续节点。
    """

    if isinstance(content, str):
        return content
    return str(content)


def add_line_numbers(code: str) -> str:
    """给源码加行号。

    LLM 做 code review 时，如果输入没有行号，它经常会猜错定位。
    所以这里把代码变成 `0001 | ...` 的格式，让两个 agent 都基于同一份定位信息。
    """

    return "\n".join(f"{line_no:04d} | {line}" for line_no, line in enumerate(code.splitlines(), 1))


def build_human_review_message(state: CodeCrossState) -> HumanMessage:
    """构造发给 reviewer agent 的用户消息。"""

    return HumanMessage(
        content=f"""\
请审查下面这份代码。

审查任务：
{state["task"]}

文件路径：
{state["file_path"]}

带行号的代码：
```text
{state["numbered_code"]}
```
"""
    )


def build_graph():
    """创建并编译 CodeCross 的 LangGraph 流程图。

    当前最小版本只有三个节点：
    1. deepseek_reviewer：从 bug / 质量角度审查；
    2. anthropic_reviewer：从安全 / 边界角度审查；
    3. aggregator：把两个 reviewer 的输出合成一份报告。

    START 同时连到两个 reviewer，这代表两个 agent 可以并行工作。
    aggregator 等两个 reviewer 都完成后再执行。
    """

    require_env("DEEPSEEK_API_KEY")
    require_env("ANTHROPIC_API_KEY")

    deepseek_llm = init_chat_model(
        model=DEEPSEEK_MODEL,
        model_provider="deepseek",
        temperature=0,
    )
    anthropic_llm = init_chat_model(
        model=ANTHROPIC_MODEL,
        model_provider="anthropic",
        temperature=0,
    )

    def deepseek_reviewer(state: CodeCrossState) -> dict[str, str]:
        """Agent 1：偏 bug / 可维护性的 reviewer。"""

        try:
            response = deepseek_llm.invoke(
                [
                    SystemMessage(content=DEEPSEEK_REVIEWER_PROMPT),
                    build_human_review_message(state),
                ]
            )
            return {"deepseek_review": content_to_text(response.content)}
        except Exception as exc:
            # 学习版里先不中断整张图：一个 agent 失败时，另一个 agent 仍然可以产出结果。
            return {"deepseek_review": f"DeepSeek Reviewer 调用失败：{type(exc).__name__}: {exc}"}

    def anthropic_reviewer(state: CodeCrossState) -> dict[str, str]:
        """Agent 2：偏安全 / 边界条件的 reviewer。"""

        try:
            response = anthropic_llm.invoke(
                [
                    SystemMessage(content=ANTHROPIC_REVIEWER_PROMPT),
                    build_human_review_message(state),
                ]
            )
            return {"anthropic_review": content_to_text(response.content)}
        except Exception as exc:
            # 这里常见原因是 ANTHROPIC_API_KEY 无效，或 ANTHROPIC_BASE_URL 指向了非 Anthropic 协议的网关。
            return {"anthropic_review": f"Anthropic Reviewer 调用失败：{type(exc).__name__}: {exc}"}

    def aggregator(state: CodeCrossState) -> dict[str, str]:
        """汇总节点。

        为了保持“只有两个 agent”，这里不用第三个 LLM 做裁判，
        而是先做确定性的 Markdown 汇总。之后你可以把这里升级成：
        - 规则去重；
        - LLM adjudicator；
        - Decision Defer；
        - 写入短期记忆。
        """

        final_report = f"""\
# CodeCross Mini Review Report

## Review Target

- File: `{state["file_path"]}`
- Task: {state["task"]}
- Reviewer Agents: DeepSeek Reviewer, Anthropic Reviewer

## Cross-Agent Summary

下面是两个 reviewer agent 的原始审查结论。当前最小版本先不做复杂去重，
你可以人工对比两个视角：如果两个 agent 都提到同类问题，通常优先级更高；
如果只有一个 agent 提到，则可以作为候选问题继续确认。

## DeepSeek Reviewer

{state.get("deepseek_review", "DeepSeek Reviewer 没有返回结果。")}

## Anthropic Reviewer

{state.get("anthropic_review", "Anthropic Reviewer 没有返回结果。")}
"""
        return {"final_report": final_report}

    graph = StateGraph(CodeCrossState)
    graph.add_node("deepseek_reviewer", deepseek_reviewer)
    graph.add_node("anthropic_reviewer", anthropic_reviewer)
    graph.add_node("aggregator", aggregator)

    graph.add_edge(START, "deepseek_reviewer")
    graph.add_edge(START, "anthropic_reviewer")
    graph.add_edge(["deepseek_reviewer", "anthropic_reviewer"], "aggregator")
    graph.add_edge("aggregator", END)

    return graph.compile()


def load_code(file_path: str | None) -> tuple[str, str]:
    """读取待审查代码。

    不传 --file 时使用内置 demo，方便你直接运行观察流程。
    """

    if not file_path:
        return "demo_buggy.py", DEMO_CODE

    path = Path(file_path).expanduser().resolve()
    return str(path), path.read_text(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    """解析学习脚本的命令行参数。"""

    parser = argparse.ArgumentParser(description="CodeCross mini prototype powered by LangGraph.")
    parser.add_argument("--file", help="要审查的代码文件。不传时使用内置 demo。")
    parser.add_argument(
        "--task",
        default="请做一次简洁但严格的 code review，重点关注真实风险。",
        help="传给两个 reviewer agent 的审查目标。",
    )
    return parser.parse_args()


def main() -> None:
    """脚本入口：读取代码、构造初始状态、运行 LangGraph。"""

    args = parse_args()
    file_path, code = load_code(args.file)

    initial_state: CodeCrossState = {
        "task": args.task,
        "file_path": file_path,
        "code": code,
        "numbered_code": add_line_numbers(code),
    }

    app = build_graph()
    result = app.invoke(initial_state)
    print(result["final_report"])


if __name__ == "__main__":
    main()
