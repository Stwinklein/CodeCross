"""LangGraph 多 Agent 学习脚本。

这个文件演示两个 create_agent 如何通过 StateGraph 串联：
先由 add_agent 处理加法，再把结果交给 multiply_agent。
它是 CodeCross 多 reviewer fan-out/fan-in 之前的最小概念练习。
"""

from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from langchain.agents import create_agent
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


# 1. 定义 State
# class AgentState(TypedDict):
#     messages: Annotated[list, add_messages]
# 2. 和单agent不同，多agent需要状态流转，需要定义状态
class AgentState(TypedDict):
    """多 Agent 串联时显式传递的中间状态。"""

    question: str
    add_result: str
    multiply_result: str

# 2. 定义工具
@tool
def add(a: int, b: int) -> int:
    """计算两个整数的和。"""
    return a + b


@tool
def multiply(a: int, b: int) -> int:
    """计算两个整数的乘积。"""
    return a * b


tools = [add]


# 3. 定义模型，并绑定工具
llm = init_chat_model(
    model="deepseek-chat",
    model_provider="deepseek",
)
#
# llm_with_tools = llm.bind_tools(tools)
# 3. 创建两个agent
add_agent = create_agent(
    model=llm,
    tools=[add],
    system_prompt="你是加法 Agent。你只能负责整数加法，不要做乘法。",
)
multiply_agent = create_agent(
    model=llm,
    tools=[multiply],
    system_prompt="你是乘法 Agent。你只能负责整数乘法，不要做乘法。",
)
# 4. 定义 LLM 节点
# SYSTEM_PROMPT="你是计算整数和的专家，只计算整数和不计算其他东西"
# def llm_node(state: AgentState):
#     response = llm_with_tools.invoke([
#         SystemMessage(content=SYSTEM_PROMPT),
#         *state["messages"]
#     ])
#     return {
#         "messages": [response]
#     }
def add_node(state: AgentState):
    """加法 Agent 节点：只负责提取并完成加法部分。"""

    print("进入 add_agent 节点")
    question = state["question"]
    response = add_agent.invoke({
        "messages": [
            HumanMessage(content=f"请只计算加法部分：{question}，只返回加法结果。")
        ]
    })
    return {
        "add_result": response["messages"][-1].content
    }
def multiply_node(state: AgentState):
    """乘法 Agent 节点：读取 add_result，再完成乘法部分。"""

    print("进入 mult_agent 节点")
    question = state["question"]
    add_result = state["add_result"]
    response = multiply_agent.invoke({
        "messages": [
            HumanMessage(content=f"已知加法结果是 {add_result}，请把它乘以 5，只返回最终结果。")
        ]
    })
    return {
        "multiply_result": response["messages"][-1].content
    }
# 5. 定义是否继续调用工具
def should_continue(state: AgentState):
    """旧 ToolNode 示例残留的条件函数，用于对比单 Agent 写法。"""

    last_message = state["messages"][-1]

    if last_message.tool_calls:
        return "tools"

    return END


# 6. 创建图,就是流程图，node就是流程图节点，conditional就是判断的菱形
builder = StateGraph(AgentState)

builder.add_node("add_agent", add_node)
builder.add_node("multiply_agent", multiply_node)

builder.add_edge(START, "add_agent")
builder.add_edge("add_agent", "multiply_agent")
builder.add_edge("multiply_agent", END)


# 7. 编译成 agent
agent = builder.compile()


# 8. 调用 agent
# result = agent.invoke({
#     "question": "请帮我计算 3 加 4，再乘以 5"
# })
#
# print("原始问题：", result["question"])
# print("加法结果：", result["add_result"])
# print("最终结果：", result["multiply_result"])
for chunk in agent.stream(
    {"question": "请帮我计算 3 加 4，再乘以 5"},
    stream_mode="updates"
):
    print(chunk)
