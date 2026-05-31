"""CodeCross V1 package.

这里暴露最常用的外部 API，方便以后写：

    from codecross import load_project_config, run_workflow

内部模块仍然可以按职责单独 import。
"""

from .config import ProjectConfig, load_project_config
from .workflow import run_workflow

__all__ = ["ProjectConfig", "load_project_config", "run_workflow"]
