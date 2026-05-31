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
