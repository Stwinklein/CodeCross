from __future__ import annotations

"""CodeCross CLI 入口。

这个模块负责读取 diff、加载配置、运行 workflow，并把结果打印或写入文件。
真正的业务逻辑都在 workflow/agents/board/executor 里。
"""

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import load_project_config
from .workflow import run_workflow


DEMO_DIFF = """\
diff --git a/examples/sample_project/math_utils.py b/examples/sample_project/math_utils.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/examples/sample_project/math_utils.py
@@
+def safe_divide(a, b):
+    return a / b
"""


def read_diff(args: argparse.Namespace, project_root: Path) -> str:
    """根据 CLI 参数读取 diff。

    优先级：--diff-text > --diff 文件 > examples/diffs demo > 内置 demo。
    """

    if args.diff_text:
        return args.diff_text
    if args.diff:
        return Path(args.diff).read_text(encoding="utf-8")
    demo_diff = project_root / "examples" / "diffs" / "safe_divide.diff"
    if args.demo or demo_diff.exists():
        return demo_diff.read_text(encoding="utf-8") if demo_diff.exists() else DEMO_DIFF
    return DEMO_DIFF


def to_jsonable(value: Any) -> Any:
    """把 Pydantic/dataclass/state 对象转换成 json.dumps 能处理的结构。"""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items() if key != "config"}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def parse_args() -> argparse.Namespace:
    """定义命令行参数。"""

    parser = argparse.ArgumentParser(description="CodeCross V1 multi-model code review runner.")
    parser.add_argument("--diff", help="Path to a unified diff file.")
    parser.add_argument("--diff-text", help="Inline unified diff text.")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root used as cwd/PYTHONPATH for generated pytest cases.",
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Use deterministic local agents instead of calling configured models.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the bundled safe_divide demo.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of Markdown.")
    parser.add_argument("--output", help="Write the report to this file.")
    return parser.parse_args()


def main() -> None:
    """CLI 主函数。"""

    args = parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_project_config(project_root, mock_llm=args.mock_llm)
    diff = read_diff(args, project_root)

    result = run_workflow(diff, project_root=project_root, config=config)
    # Markdown 适合人看；JSON 适合后续接 Web UI、CI 或其他工具。
    if args.json:
        output = json.dumps(to_jsonable(result), ensure_ascii=False, indent=2)
    else:
        output = result["final_report"]

    # 可选写入文件，同时仍然打印到控制台，便于本地直接观察结果。
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
