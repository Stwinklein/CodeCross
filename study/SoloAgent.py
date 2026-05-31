"""LangGraph 单 Agent 学习脚本。

这个文件演示最基础的 ToolNode 循环：LLM 判断是否需要调用工具，
ToolNode 执行 add/multiply，结果再回到 LLM 继续推理。
正式项目代码已经迁移到 codecross/，study/ 只作为学习材料保留。
"""

from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


# 1. 定义 State
class AgentState(TypedDict):
    """单 Agent 图里的状态：只需要维护一串 messages。"""

    messages: Annotated[list, add_messages]


# 2. 定义工具
@tool
def add(a: int, b: int) -> int:
    """计算两个整数的和。"""
    return a + b


@tool
def multiply(a: int, b: int) -> int:
    """计算两个整数的乘积。"""
    return a * b


tools = [add, multiply]


# 3. 定义模型，并绑定工具
llm = init_chat_model(
    model="deepseek-chat",
    model_provider="deepseek",
)

llm_with_tools = llm.bind_tools(tools)


# 4. 定义 LLM 节点
SYSTEM_PROMPT="你是计算整数和的专家，只计算整数和不计算其他东西"
def llm_node(state: AgentState):
    """LLM 节点：读取历史消息，必要时产生 tool call。"""

    response = llm_with_tools.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        *state["messages"]
    ])
    return {
        "messages": [response]
    }


# 5. 定义是否继续调用工具
def should_continue(state: AgentState):
    """条件边：如果最后一条消息有 tool_calls，就去 ToolNode。"""

    last_message = state["messages"][-1]

    if last_message.tool_calls:
        return "tools"

    return END


# 6. 创建图
builder = StateGraph(AgentState)

builder.add_node("llm", llm_node)
builder.add_node("tools", ToolNode(tools))

builder.add_edge(START, "llm")

builder.add_conditional_edges(
    "llm",
    should_continue,
    {
        "tools": "tools",
        END: END,
    }
)

builder.add_edge("tools", "llm")


# 7. 编译成 agent
agent = builder.compile()


# 8. 调用 agent
result = agent.invoke({
    "messages": [
        HumanMessage(content="请帮我计算 3 加 4，再乘以 5")
    ]
})

for message in result["messages"]:
    message.pretty_print()
