"""CodeCross 的平台无关评测核心。

这里故意不把评分规则写死在 LangSmith 回调里。这样同一套 case 和 scorer 可以在
本地 pytest、GitHub CI、LangSmith 实验以及未来其它平台中复用，避免平台迁移时
丢失历史口径。
"""

from .models import EvaluationCase, EvaluationOutput, EvaluationScores, SuiteReport
from .runner import load_cases_jsonl, run_local_suite

__all__ = [
    "EvaluationCase",
    "EvaluationOutput",
    "EvaluationScores",
    "SuiteReport",
    "load_cases_jsonl",
    "run_local_suite",
]

