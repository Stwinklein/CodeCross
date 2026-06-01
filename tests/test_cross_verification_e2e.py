"""ReproAgent 真实 LLM 端到端测试（Issue #2 Step 2）。

此测试调用真实模型，不走 mock。两个目的：

1. 证明 ReproAgent 在生产环境下真的能从 finding + 测试用例里产出可读的复现说明，
   而不是 mock_llm 路径下永远返回 supported 占位。
2. 顺手验证“跨家族独立性”——默认配置下 ReproAgent 用 Anthropic，reviewer 用
   DeepSeek + Anthropic，端到端跑一遍能让我们看到模型是否真的输出中文 reasoning。

跳过条件：
- ANTHROPIC_API_KEY 缺失 -> 直接跳过；这是 ReproAgent 默认 provider。
- 用户也可以显式设置 ``CODECROSS_SKIP_REAL_LLM=1`` 跳过，便于 CI 控制。

测试断言：
- payload.status 不是 abstain（abstain 不算实际验证）
- payload.reasoning 非空且包含中文（验证 prompt 真的把中文要求传过去了）
- evidence_kind/层级元数据正确
"""

from __future__ import annotations

import os
import re

import pytest

from codecross.config import load_project_config
from codecross.cross_verification.repro_agent import run_repro_agent
from codecross.schemas import Finding, LineRange, TestCase

pytestmark = pytest.mark.skipif(
    os.getenv("CODECROSS_SKIP_REAL_LLM") == "1"
    or not os.getenv("ANTHROPIC_API_KEY"),
    reason="real-LLM e2e: requires ANTHROPIC_API_KEY (and not skipped via CODECROSS_SKIP_REAL_LLM)",
)


_CHINESE_CHAR = re.compile(r"[\u4e00-\u9fff]")


def test_repro_agent_real_model_handles_unsupported_language():
    """跑一次真实 ReproAgent，要求它对一个 JavaScript finding 给出复现思路。

    这里特意挑一个非常具体的 finding：JS 路由处理器没有验证 query 参数，
    会让模型在“能否复现”这件事上有清晰判断面。即便模型最终给 supported 而非
    confirmed，也证明 fallback 链路真的接通了模型并解析了 JSON。
    """

    finding = Finding(
        finding_id="finding_real_repro",
        source_reviewer="logic_reviewer",
        model="deepseek/test",
        category="logic",
        severity="high",
        file_path="src/server/routes.js",
        line_range=LineRange(start=12, end=28),
        problem=(
            "Route handler /api/users/:id does not validate that req.params.id is a "
            "non-empty string before calling db.findUserById, so an empty path segment "
            "leads to an unhandled exception that crashes the request worker."
        ),
        evidence=(
            "The diff adds the new route but never narrows req.params.id; existing "
            "middleware does not run for this path either."
        ),
        impact="A crafted request can hard-crash the request worker and impact other tenants.",
        suggestion="Validate req.params.id with a 400 short-circuit before the DB call.",
    )

    test_case = TestCase(
        source_agent="test_author",
        related_finding_ids=[finding.finding_id],
        test_type="llm_cross_verification",
        target_file="src/server/routes.js",
        target_symbol="handleGetUserById",
        test_name="cross_verify_get_user_by_id_missing_id",
        test_code=(
            "// pytest 不可用，请用人工或集成测试复现：\n"
            "// 1) 启动 dev server\n"
            "// 2) curl http://localhost:3000/api/users/  (注意尾部空 id)\n"
            "// 3) 观察 worker 是否崩溃 / 返回 500"
        ),
        expected_behavior="A missing id should produce a 400 response, not a worker crash.",
        target_language="javascript",
        cross_verification_trigger="unsupported_language",
    )

    config = load_project_config(mock_llm=False)
    payload = run_repro_agent(
        finding=finding,
        test_case=test_case,
        code_context=None,
        config=config,
        trigger="unsupported_language",
    )

    # 1) 端到端走通：必须落在五个有效状态之一，且 evidence_kind 由 board 校验。
    assert payload.status in {
        "confirmed",
        "supported",
        "refuted",
        "inconclusive",
    }, f"unexpected status from real ReproAgent: {payload!r}"

    # 2) 真实模型（非 mock）层级标记必须是 repro_agent。
    assert payload.layer == "repro_agent"
    # mock 标签会包含 "mock/"；如果出现，说明 fallback 没真正调用模型。
    assert not payload.model.startswith("mock/"), (
        f"expected real model tag, got {payload.model!r}"
    )

    # 3) 中文 reasoning 检查——验证 system prompt 里“所有自然语言用中文”这一条
    #    真的被尊重；如果模型只回英文，说明 prompt 或者上下游链路出问题。
    assert payload.reasoning, "ReproAgent reasoning must not be empty"
    assert _CHINESE_CHAR.search(payload.reasoning), (
        f"expected Chinese reasoning, got: {payload.reasoning!r}"
    )
