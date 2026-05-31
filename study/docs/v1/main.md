# `codecross/main.py` 解析

## 文件设计说明

`main.py` 是 CodeCross 的 CLI 入口。

它负责：

```text
解析命令行参数
读取 diff
加载配置
调用 run_workflow
输出 Markdown 或 JSON
可选写入文件
```

它不负责：

- LangGraph 编排
- 模型调用
- Board 证据规则
- pytest 执行

这样设计可以让 CLI 很薄，未来接 Web UI、CI、GitHub PR 时，也可以复用 `run_workflow`。

## 常量

### `DEMO_DIFF`

内置 demo diff。

内容是新增：

```python
def safe_divide(a, b):
    return a / b
```

这个 demo 故意保留除零 bug，用于验证 CodeCross 能否发现并测试它。

## 函数详解

### `read_diff(args, project_root)`

根据 CLI 参数读取 diff。

优先级：

```text
--diff-text
--diff 文件路径
examples/diffs/safe_divide.diff
内置 DEMO_DIFF
```

这样设计方便三种用法：

1. 直接跑 demo。
2. 从文件读真实 diff。
3. 命令行粘贴小 diff。

### `to_jsonable(value)`

把 workflow 返回的复杂对象转换成 `json.dumps` 能处理的结构。

处理对象：

- Pydantic BaseModel
- dataclass
- dict
- list

注意它会跳过 `config` 字段：

```python
if key != "config"
```

原因：config 里有模型设置和环境变量名，不适合完整输出到 JSON 报告。

### `parse_args()`

定义 CLI 参数。

参数：

- `--diff`
- `--diff-text`
- `--project-root`
- `--mock-llm`
- `--demo`
- `--json`
- `--output`

#### `--mock-llm`

使用确定性 mock agents，不调用真实模型。

适合：

- 单测
- 本地快速调试
- 不消耗额度

#### `--json`

输出完整结构化 JSON。

适合：

- LangSmith eval
- CI 机器解析
- Web UI 后端消费

#### `--output`

把报告写入文件。

### `main()`

CLI 主函数。

流程：

1. 解析参数。
2. 解析项目根目录。
3. 加载配置：

```python
config = load_project_config(project_root, mock_llm=args.mock_llm)
```

4. 读取 diff。
5. 运行 workflow：

```python
result = run_workflow(diff, project_root=project_root, config=config)
```

6. 根据 `--json` 决定输出格式。
7. 如果指定 `--output`，写文件。
8. 打印到控制台。

## 启动方式

推荐用 module 启动：

```powershell
.\.venv\Scripts\python.exe -m codecross.main --demo --project-root .
```

PyCharm 中也应该选择：

```text
module: codecross.main
```

不要直接运行：

```text
codecross/main.py
```

因为文件里使用了相对导入：

```python
from .config import load_project_config
```

直接 script 模式会报：

```text
ImportError: attempted relative import with no known parent package
```

## 这个文件的关键思想

`main.py` 是薄入口。它只负责把外部输入转换成 workflow 调用，不把业务逻辑塞到 CLI 里。

