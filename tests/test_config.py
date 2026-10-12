"""配置层测试。

这些测试保证 .env 中的两个渠道会被正确映射成两个 reviewer。
"""

from codecross.config import load_project_config


def test_load_project_config_uses_two_env_channels(monkeypatch):
    """DeepSeek 和 Anthropic 环境变量应该生成两个启用 reviewer。"""

    # monkeypatch 只影响当前测试，不会改真实 .env。
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    config = load_project_config(mock_llm=True)

    reviewers = config.enabled_reviewers()
    assert [reviewer.display_name for reviewer in reviewers] == [
        "logic_reviewer",
        "security_maintainability_reviewer",
    ]
    assert reviewers[0].provider == "deepseek"
    assert reviewers[0].model == "deepseek-test"
    assert reviewers[1].provider == "anthropic"
    assert reviewers[1].model == "claude-test"


def test_human_alignment_uses_two_independent_anthropic_roles_by_default(monkeypatch):
    """公开 human benchmark 默认测强模型上限，但两个角色和 Prompt 仍保持独立。"""

    monkeypatch.delenv("CODECROSS_HUMAN_ALIGNMENT_PRIMARY_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    config = load_project_config(
        mock_llm=True,
        evaluation_mode="review_only",
        review_profile="human_review_alignment",
    )

    reviewers = config.enabled_reviewers()
    assert [item.provider for item in reviewers] == ["anthropic", "anthropic"]
    assert [item.model for item in reviewers] == ["claude-test", "claude-test"]
    assert reviewers[0].focus != reviewers[1].focus


def test_human_alignment_primary_can_be_switched_back_to_deepseek(monkeypatch):
    monkeypatch.setenv("CODECROSS_HUMAN_ALIGNMENT_PRIMARY_PROVIDER", "deepseek")
    config = load_project_config(
        mock_llm=True,
        evaluation_mode="review_only",
        review_profile="human_review_alignment",
    )

    assert [item.provider for item in config.enabled_reviewers()] == [
        "deepseek",
        "anthropic",
    ]


def test_load_project_config_preserves_structured_command_override():
    """用户 argv 覆盖应进入 RunnerConfig，detector 命令仍保留用于审计。"""

    override = ["python", "-m", "unittest", "discover"]
    config = load_project_config(mock_llm=True, test_command_override=override)

    assert config.runner_config.test_command_override == override
    # loader 复制列表，调用方随后修改 override 不会影响正在运行的配置。
    assert config.runner_config.test_command_override is not override
    assert config.test_command
