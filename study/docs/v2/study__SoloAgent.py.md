# `study/SoloAgent.py` 解析

## 文件作用
单 Agent + ToolNode 学习脚本，演示一个 LLM 节点如何循环调用工具直到得到最终答案。

- 源码行数：116
- 主要依赖：`pathlib`、`typing`、`dotenv`、`typing_extensions`、`langchain`、`langchain_core`、`langgraph`

## 函数与类

### `AgentState`（类，第 27 行）
- 签名：`class AgentState(TypedDict)`
- 作用：单 Agent 图里的状态：只需要维护一串 messages。
- 主要字段/属性：
  - `messages: Annotated[list, add_messages]`

### `add`（函数，第 35 行）
- 签名：`def add(a: int, b: int) -> int`
- 作用：计算两个整数的和。

### `multiply`（函数，第 41 行）
- 签名：`def multiply(a: int, b: int) -> int`
- 作用：计算两个整数的乘积。

### `llm_node`（函数，第 60 行）
- 签名：`def llm_node(state: AgentState)`
- 作用：LLM 节点：读取历史消息，必要时产生 tool call。

### `should_continue`（函数，第 73 行）
- 签名：`def should_continue(state: AgentState)`
- 作用：条件边：如果最后一条消息有 tool_calls，就去 ToolNode。

## 学习提示
这是学习/原型代码，重点看它如何用更小的例子解释正式实现里的设计。
