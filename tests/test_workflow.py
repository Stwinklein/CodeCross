"""端到端 workflow 测试。

使用 mock_llm 保证不依赖外部模型，但仍然跑完整 LangGraph + pytest executor。
"""

from pathlib import Path

import codecross.workflow as workflow
from codecross.config import load_project_config
from codecross.schemas import CodeContext, CodeContextSnippet, ReviewerResult
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


def test_frozen_context_override_reaches_every_reviewer_before_review(monkeypatch):
    """公开评测的 frozen context 必须在并行 Reviewer 启动前生效。

    这是输入协议的端到端回归测试：仅验证 graph 状态传递，不访问任何真实模型。
    如果未来有人又把 override 移到结束节点，测试会同时发现本地检索被调用、Reviewer
    收到错误上下文或 review-only 停止原因缺失。
    """

    frozen = CodeContext(
        changed_files=["src/example.py"],
        contains_diff=True,
        snippets=[
            CodeContextSnippet(
                kind="diagnostic",
                file_path="<swe-prbench/config_A>",
                start_line=1,
                end_line=2,
                reason="Frozen public benchmark context.",
                content="FROZEN_CONTEXT_MARKER\n+changed_line",
            )
        ],
    )
    seen_contexts: list[CodeContext | None] = []

    def fail_if_local_context_is_retrieved(**kwargs):
        raise AssertionError("存在 frozen override 时不应扫描本地仓库")

    def fake_reviewer_agent(**kwargs):
        seen_contexts.append(kwargs["code_context"])
        reviewer = kwargs["reviewer"]
        return ReviewerResult(
            reviewer_name=reviewer.display_name,
            model=f"{reviewer.provider}/{reviewer.model}",
        )

    monkeypatch.setattr(workflow, "context_retriever_agent", fail_if_local_context_is_retrieved)
    monkeypatch.setattr(workflow, "reviewer_agent", fake_reviewer_agent)
    config = load_project_config(
        Path.cwd(),
        mock_llm=False,
        evaluation_mode="review_only",
        review_profile="human_review_alignment",
    )

    result = run_workflow(
        "diff --git a/src/example.py b/src/example.py\n+changed_line\n",
        project_root=Path.cwd(),
        config=config,
        code_context_override=frozen,
    )

    assert len(seen_contexts) == len(config.enabled_reviewers()) == 2
    assert all(context is frozen for context in seen_contexts)
    assert result["code_context"] is frozen
    assert result["stop_reason"] == "review_only_complete"
    assert result["board_summary"].stop_reason == "review_only_complete"
