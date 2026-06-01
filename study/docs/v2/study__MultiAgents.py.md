# `study/MultiAgents.py` 解析

## 文件作用
多 Agent/LangGraph 学习脚本，把加法和乘法拆成两个节点串联，演示显式状态传递。

- 源码行数：148
- 主要依赖：`pathlib`、`typing`、`dotenv`、`langchain`、`typing_extensions`、`langchain_core`、`langgraph`

## 函数与类

### `AgentState`（类，第 31 行）
- 签名：`class AgentState(TypedDict)`
- 作用：多 Agent 串联时显式传递的中间状态。
- 主要字段/属性：
  - `question: str`
  - `add_result: str`
  - `multiply_result: str`

### `add`（函数，第 40 行）
- 签名：`def add(a: int, b: int) -> int`
- 作用：计算两个整数的和。

### `multiply`（函数，第 46 行）
- 签名：`def multiply(a: int, b: int) -> int`
- 作用：计算两个整数的乘积。

### `add_node`（函数，第 82 行）
- 签名：`def add_node(state: AgentState)`
- 作用：加法 Agent 节点：只负责提取并完成加法部分。

### `multiply_node`（函数，第 95 行）
- 签名：`def multiply_node(state: AgentState)`
- 作用：乘法 Agent 节点：读取 add_result，再完成乘法部分。

### `should_continue`（函数，第 110 行）
- 签名：`def should_continue(state: AgentState)`
- 作用：旧 ToolNode 示例残留的条件函数，用于对比单 Agent 写法。

## 学习提示
这是学习/原型代码，重点看它如何用更小的例子解释正式实现里的设计。
