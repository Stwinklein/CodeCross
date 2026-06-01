"""端到端 workflow 测试。

使用 mock_llm 保证不依赖外部模型，但仍然跑完整 LangGraph + pytest executor。
"""

from pathlib import Path

from codecross.ci import build_pr_comment
from codecross.config import load_project_config
from codecross.workflow import run_workflow


def test_workflow_runs_v1_demo_with_two_reviewers():
    """V1 demo 应该产出两个 reviewer、失败测试证据和 high risk 结论。"""

    root = Path.cwd()
    diff = (root / "examples" / "diffs" / "safe_divide.diff").read_text(encoding="utf-8")
    config = load_project_config(root, mock_llm=True)

    result = run_workflow(diff, project_root=root, config=config)

    assert result["board_summary"].reviewer_count == 2
    assert result["code_context"].changed_files == ["examples/sample_project/math_utils.py"]
    assert result["code_context"].snippets
    assert result["board_summary"].finding_count >= 1
    assert result["board_summary"].test_case_count >= 2
    assert result["board_summary"].failed_count >= 1
    assert any(
        case.test_name == "test_safe_divide_normal_division_returns_quotient"
        for case in result["all_test_cases"]
    )
    assert result["evaluator_report"].risk_level == "high"
    assert result["high_risk_review"] is not None
    assert "CodeCross Bilingual Final Review Report" in result["final_report"]
    assert "## English Report" in result["final_report"]
    assert "## 中文报告" in result["final_report"]
    assert "CodeCross Final Review Report" in result["final_report_en"]
    assert "CodeCross 最终审查报告（中文）" in result["final_report_zh"]
    assert "## Code Context" in result["final_report_en"]

    comment = build_pr_comment(result, mock_llm=True)
    assert "## 🇬🇧 English" in comment
    assert "### Findings at a glance" in comment
    assert "### Action checklist" in comment
    assert "## 🇨🇳 中文" in comment
    assert "### 修复清单" in comment
