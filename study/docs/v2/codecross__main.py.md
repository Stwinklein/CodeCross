# `codecross/main.py` 解析

## 文件作用
本地命令行入口，读取 diff、加载配置、启动 workflow，并输出 Markdown 或 JSON。

- 源码行数：107
- 主要依赖：`argparse`、`json`、`dataclasses`、`pathlib`、`typing`、`pydantic`、`config`、`workflow`

## 函数与类

### `read_diff`（函数，第 33 行）
- 签名：`def read_diff(args: argparse.Namespace, project_root: Path) -> str`
- 作用：根据 CLI 参数读取 diff。

### `to_jsonable`（函数，第 49 行）
- 签名：`def to_jsonable(value: Any) -> Any`
- 作用：把 Pydantic/dataclass/state 对象转换成 json.dumps 能处理的结构。

### `parse_args`（函数，第 63 行）
- 签名：`def parse_args() -> argparse.Namespace`
- 作用：定义命令行参数。

### `main`（函数，第 85 行）
- 签名：`def main() -> None`
- 作用：CLI 主函数。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
