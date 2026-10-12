"""LLM 适配层测试。

这些测试不调用真实模型，只验证 CodeCross 传给 LangChain 的参数和错误边界。
"""

import pytest
from pydantic import BaseModel

from codecross import llm
from codecross.config import ModelSettings
from codecross.llm import ModelCallError


class _RepairEnvelope(BaseModel):
    items: list[dict[str, str]]


def test_anthropic_uses_current_max_tokens_parameter(monkeypatch):
    """Anthropic provider 应使用 max_tokens，而不是旧字段 max_tokens_to_sample。"""

    captured: dict[str, object] = {}

    def fake_init_chat_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm, "init_chat_model", fake_init_chat_model)
    settings = ModelSettings(
        role="high_risk_reviewer",
        provider="anthropic",
        model="claude-test",
        max_tokens=1234,
    )

    llm.create_chat_model(settings)

    assert captured["max_tokens"] == 1234
    assert "max_tokens_to_sample" not in captured


def test_call_model_wraps_recoverable_runtime_error(monkeypatch):
    """网络/超时类错误应包装成 ModelCallError，方便 agent 层 fallback。"""

    class FailingModel:
        def invoke(self, messages):
            raise TimeoutError("model timed out")

    monkeypatch.setattr(llm, "init_chat_model", lambda **kwargs: FailingModel())
    settings = ModelSettings(role="planner", provider="deepseek", model="deepseek-test")

    with pytest.raises(ModelCallError):
        llm.call_model(settings, [("user", "hello")])


def test_call_model_does_not_wrap_local_programming_error(monkeypatch):
    """非 provider/网络异常应暴露原始类型，避免代码 bug 被伪装成模型失败。"""

    class LocalBug(RuntimeError):
        pass

    class FailingModel:
        def invoke(self, messages):
            raise LocalBug("local bug")

    monkeypatch.setattr(llm, "init_chat_model", lambda **kwargs: FailingModel())
    settings = ModelSettings(role="planner", provider="deepseek", model="deepseek-test")

    with pytest.raises(LocalBug):
        llm.call_model(settings, [("user", "hello")])


def test_parse_json_model_repairs_missing_comma_but_still_validates_schema():
    """DeepSeek 常见的相邻 object 缺逗号应本地修复，无需再次模型调用。"""

    parsed = llm.parse_json_model(
        _RepairEnvelope,
        '{"items": [{"name": "first"}\n {"name": "second"}]}',
    )

    assert parsed.items == [{"name": "first"}, {"name": "second"}]


def test_parse_json_model_does_not_bypass_pydantic_validation():
    with pytest.raises(Exception):
        llm.parse_json_model(_RepairEnvelope, '{"items": "not-a-list",}')


def test_model_call_metrics_are_scoped_and_count_failures(monkeypatch):
    """一次 case 内的成功/失败调用都应计数，with 外不能污染下一次评测。"""

    class Response:
        content = "ok"

    class ControlledModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("second call failed")
            return Response()

    model = ControlledModel()
    monkeypatch.setattr(llm, "init_chat_model", lambda **kwargs: model)
    settings = ModelSettings(role="planner", provider="deepseek", model="deepseek-test")

    with llm.capture_model_call_metrics() as metrics:
        assert llm.call_model(settings, [("user", "first")]) == "ok"
        with pytest.raises(ModelCallError):
            llm.call_model(settings, [("user", "second")])

    assert metrics.call_count == 2
    assert metrics.failure_count == 1
    assert metrics.calls_by_role == {"planner": 2}
    assert metrics.duration_ms >= 0
