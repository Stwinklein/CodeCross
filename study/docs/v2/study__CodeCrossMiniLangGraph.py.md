# `study/CodeCrossMiniLangGraph.py` 解析

## 文件作用
学习版 LangGraph CodeCross，用较少节点演示 planner、reviewer、evaluator、high-risk-review 的状态流转。

- 源码行数：316
- 主要依赖：`argparse`、`os`、`pathlib`、`typing`、`dotenv`、`langchain`、`langchain_core`、`langgraph`

## 函数与类

### `CodeCrossState`（类，第 43 行）
- 签名：`class CodeCrossState(TypedDict)`
- 作用：LangGraph 在节点之间传递的状态。
- 主要字段/属性：
  - `task: str`
  - `file_path: str`
  - `code: str`
  - `numbered_code: str`
  - `deepseek_review: str`
  - `anthropic_review: str`
  - `final_report: str`

### `require_env`（函数，第 114 行）
- 签名：`def require_env(name: str) -> None`
- 作用：提前检查环境变量，报错时比 SDK 自己的异常更好懂。

### `content_to_text`（函数，第 123 行）
- 签名：`def content_to_text(content: Any) -> str`
- 作用：把 LangChain message.content 统一转成字符串。

### `add_line_numbers`（函数，第 135 行）
- 签名：`def add_line_numbers(code: str) -> str`
- 作用：给源码加行号。

### `build_human_review_message`（函数，第 145 行）
- 签名：`def build_human_review_message(state: CodeCrossState) -> HumanMessage`
- 作用：构造发给 reviewer agent 的用户消息。

### `build_graph`（函数，第 166 行）
- 签名：`def build_graph()`
- 作用：创建并编译 CodeCross 的 LangGraph 流程图。

### `load_code`（函数，第 271 行）
- 签名：`def load_code(file_path: str | None) -> tuple[str, str]`
- 作用：读取待审查代码。

### `parse_args`（函数，第 284 行）
- 签名：`def parse_args() -> argparse.Namespace`
- 作用：解析学习脚本的命令行参数。

### `main`（函数，第 297 行）
- 签名：`def main() -> None`
- 作用：脚本入口：读取代码、构造初始状态、运行 LangGraph。

## 学习提示
这是学习/原型代码，重点看它如何用更小的例子解释正式实现里的设计。
