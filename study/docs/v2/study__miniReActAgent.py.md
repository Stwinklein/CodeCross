# `study/miniReActAgent.py` 解析

## 文件作用
迷你 ReAct Agent 学习脚本，展示工具、模型和 agent 执行器的最小组合。

- 源码行数：59
- 主要依赖：`dataclasses`、`typing`、`json`、`re`

## 函数与类

### `Tool`（类，第 48 行）
- 签名：`class Tool`
- 作用：工具定义 langchain的@tool装饰器本质上是 1记录工具名 2记录工具描述 3记录工具参数schema 4保存真正要执行的python函数
- 主要字段/属性：
  - `name: str`
  - `description: str`
  - `function: Callable[[str], str]`

## 学习提示
这是学习/原型代码，重点看它如何用更小的例子解释正式实现里的设计。
