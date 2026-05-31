# `codecross/__init__.py` 解析

## 文件设计说明

`__init__.py` 是 `codecross` 包的对外入口。它不放业务逻辑，只暴露最常用的 API，让外部代码可以用更短的 import 方式调用 CodeCross。

这样设计的原因是：

- 外部使用者不需要知道内部模块拆分。
- 保持包入口干净，不让初始化阶段产生副作用。
- 以后如果内部文件重构，尽量不影响外部 import。

## 实现详解

### 模块 docstring

文件开头说明这是 CodeCross V1 包入口，并给出示例：

```python
from codecross import load_project_config, run_workflow
```

这告诉读者：最常用的两个入口是加载配置和运行工作流。

### 导入并导出核心对象

```python
from .config import ProjectConfig, load_project_config
from .workflow import run_workflow
```

这里从内部模块引出：

- `ProjectConfig`：项目运行配置对象。
- `load_project_config`：从 `.env` 和默认值生成配置。
- `run_workflow`：给定 diff 后启动完整 CodeCross 工作流。

### `__all__`

```python
__all__ = ["ProjectConfig", "load_project_config", "run_workflow"]
```

它声明 `from codecross import *` 时只导出这些名字，也相当于告诉读者“包级公共 API 就是这些”。

## 调试建议

这个文件通常不用打断点。调试入口应该从：

- `main.py::main`
- `workflow.py::run_workflow`

开始。

