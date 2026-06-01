"""ReproAgent —— LLM 交叉验证 Layer 1（Issue #2 Step 2）。

ReproAgent 在 pytest 不可用时接力，让一个跨家族独立模型尝试给出
“这个 finding 真的成立吗？给我可以人工跑的复现步骤”。它的产出会被包成
:class:`CrossVerificationPayload`，写进 :class:`TestResult.cross_payload`，
再由 board 翻译成 ``cross_supported / cross_refuted / cross_inconclusive``。

读这个模块时建议带着三个问题：

1. 输入是什么？  -> finding + test_case + 可选 code_context
2. 输出是什么？  -> 一个 ReproSpec（强类型 JSON），随后被映射成 TestResult
3. 失败怎么办？  -> 任何一步异常都返回 ``inconclusive``，让 board 走人工通道

设计上刻意保持“无副作用”：不读文件、不调网络（除模型本身）、不改 board，
全部状态变更交回 executor / board，方便 mock 与单测。
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from ..config import ModelSettings, ProjectConfig
from ..llm import ModelCallError, call_model_json, json_schema_instruction
from ..schemas import (
    CodeContext,
    CrossVerificationPayload,
    EvidenceStrength,
    Finding,
    TestCase,
)


# ReproAgent 直接产出的 JSON 结构。它只是一个“原始 LLM 回包”，executor 会
# 再把它转写成 :class:`CrossVerificationPayload`。两个对象分开是为了：
#   - JSON schema（这里）只描述模型必须吐什么字段；
#   - payload（schemas.py）描述写进 TestResult 的最终结构，可以再加 layer/
#     suggested_strength 这种由代码算出的衍生字段。
class ReproSpec(BaseModel):
    """ReproAgent 的强类型回包。"""

    status: Literal["confirmed", "supported", "refuted", "inconclusive", "abstain"]
    reasoning: str = Field(default="", description="≤600 字符的中文判断理由")
    reproduction_steps: list[str] = Field(default_factory=list)
    expected_observation: str = ""
    counter_examples: list[str] = Field(default_factory=list)
    abstain_reason: str = ""


# 中文系统提示。把核心规则写得很硬：
#   - 必须返回唯一 JSON object，不要 markdown
#   - 任何不确定就 abstain，绝不“为了交差”给 confirmed
#   - 不读取其它 reviewer 的结论 -> 维持独立意见
_REPRO_SYSTEM_PROMPT = (
    "你是 CodeCross 的独立交叉验证模型，专门在 pytest 不可用时复核一个 finding。"
    "请只读取本次提供的 finding、测试意图和代码上下文，不要假设其它 reviewer 的结论。"
    "判断标准："
    "(1) confirmed=能给出无歧义的复现步骤且 finding 显然成立；"
    "(2) supported=偏向成立但需要人工再核；"
    "(3) refuted=能举出反例证明 finding 不成立；"
    "(4) inconclusive=信息不足且不能伪造结论；"
    "(5) abstain=超出能力范围或上下文不够，必须填写 abstain_reason。"
    "永远只返回唯一一个 JSON object，所有自然语言用中文。"
)


def _strength_from_status(status: str) -> EvidenceStrength:
    """把 ReproAgent 的 status 翻译成 board 用的证据强度。

    映射规则故意保守：模型给 confirmed/refuted 也只算 medium，
    因为这条证据并非真实测试。要升到 strong 需要后续的 RefuteAgent 复核。
    """

    if status in {"confirmed", "refuted"}:
        return "medium"
    if status == "supported":
        return "weak"
    return "weak"


def _truncate(text: str, limit: int) -> str:
    """轻量截断，防止 prompt 被超长上下文撑爆。

    这里没有动 reporting.py 的 ``shorten``（用户明确要求别改），
    只在 prompt 构造场景做最小够用的截断。
    """

    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _build_user_prompt(
    *,
    finding: Finding,
    test_case: TestCase,
    code_context: CodeContext | None,
    trigger: str | None,
) -> str:
    """组装给 ReproAgent 的用户消息。

    把所有已知事实展平为纯文本；让 LLM 自己决定用哪些。code_context 可能很大，
    所以只挑前几个 snippet 并截断，避免 token 溢出。
    """

    parts: list[str] = []
    parts.append(f"Trigger: {trigger or 'unknown'}")
    parts.append(f"Target language: {test_case.target_language or 'unspecified'}")
    parts.append("")
    parts.append("Finding:")
    parts.append(_truncate(finding.model_dump_json(indent=2), 4000))
    parts.append("")
    parts.append("Test case (the one pytest could not run):")
    parts.append(_truncate(test_case.model_dump_json(indent=2), 4000))

    if code_context and code_context.snippets:
        parts.append("")
        parts.append("Code context (truncated):")
        for snippet in code_context.snippets[:6]:
            parts.append(
                f"- {snippet.kind} @ {snippet.file_path}:{snippet.start_line}-{snippet.end_line}\n"
                f"  reason: {_truncate(snippet.reason, 200)}\n"
                f"  content:\n{_truncate(snippet.content, 1200)}"
            )

    parts.append("")
    parts.append(json_schema_instruction(ReproSpec))
    return "\n".join(parts)


def run_repro_agent(
    *,
    finding: Finding,
    test_case: TestCase,
    code_context: CodeContext | None,
    config: ProjectConfig,
    trigger: str | None,
) -> CrossVerificationPayload:
    """执行 ReproAgent 一次调用并返回 payload。

    返回 :class:`CrossVerificationPayload` 而不是抛异常：executor 期望任何
    cross 验证调用都给出一个 payload 写进 TestResult，所以失败也要表达成
    “status=inconclusive，reasoning=… abstain_reason=…”。

    参数：
        finding/test_case/code_context: 复核所需的全部上下文
        config: 用来取 ``repro_agent`` 角色的 ModelSettings
        trigger: 触发器返回的字面量，用于在 prompt 里告诉 LLM 为什么走 fallback
    """

    # 1) 角色查找。Step 2 强约束：必须显式注册 repro_agent，否则代码 bug
    #    应该暴露而不是悄悄走 fallback。
    settings: ModelSettings | None = config.model_config.get("repro_agent")
    if settings is None:
        return _inconclusive_payload(
            model="unknown",
            reason="缺少 repro_agent 角色配置；请在 ProjectConfig.model_config 注册。",
        )

    # 2) Mock 模式：保留 deterministic 行为，方便单测和 CI mock。
    if config.mock_llm:
        return _mock_payload(settings=settings, finding=finding, trigger=trigger)

    # 3) 真模型调用。任何已识别的“可恢复模型异常”都吞成 inconclusive，
    #    这样 fallback 不会让整条 workflow 中断；其他异常仍向上抛，避免
    #    把代码 bug 静默吃掉。
    started = time.monotonic()
    try:
        spec = call_model_json(
            settings,
            [
                ("system", _REPRO_SYSTEM_PROMPT),
                (
                    "user",
                    _build_user_prompt(
                        finding=finding,
                        test_case=test_case,
                        code_context=code_context,
                        trigger=trigger,
                    ),
                ),
            ],
            ReproSpec,
        )
    except ModelCallError as exc:
        return _inconclusive_payload(
            model=f"{settings.provider}/{settings.model}",
            reason=f"ReproAgent 调用失败：{exc}",
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    return _spec_to_payload(spec, settings=settings, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# 下面是把 ReproSpec 翻译成 CrossVerificationPayload 的纯函数。
# 单独抽出来是为了让单测可以直接构造一个 ReproSpec 走 _spec_to_payload，
# 不需要起 LLM。
# ---------------------------------------------------------------------------


def _spec_to_payload(
    spec: ReproSpec,
    *,
    settings: ModelSettings,
    duration_ms: int = 0,
) -> CrossVerificationPayload:
    """把 LLM 的强类型回包翻译成 board 用的 payload。"""

    # abstain 没有具体 reason 时强制兜一个，避免 board 看到空文本无法判断。
    abstain_reason = spec.abstain_reason
    if spec.status == "abstain" and not abstain_reason:
        abstain_reason = "ReproAgent 给出 abstain 但未填写 abstain_reason。"

    return CrossVerificationPayload(
        layer="repro_agent",
        model=f"{settings.provider}/{settings.model}",
        # abstain -> inconclusive：CrossVerificationPayload.status 没有 abstain。
        # 显式映射可以让 evaluator 始终看到 5 选 1 中的有效值。
        status="inconclusive" if spec.status == "abstain" else spec.status,
        reasoning=spec.reasoning.strip() or "ReproAgent 未给出 reasoning。",
        reproduction_steps=list(spec.reproduction_steps),
        expected_observation=spec.expected_observation.strip(),
        counter_examples=list(spec.counter_examples),
        abstain_reason=abstain_reason.strip(),
        suggested_strength=_strength_from_status(spec.status),
    )


def _inconclusive_payload(*, model: str, reason: str) -> CrossVerificationPayload:
    """统一构造“失败 / 缺配置”这种 inconclusive 路径的 payload。"""

    return CrossVerificationPayload(
        layer="repro_agent",
        model=model,
        status="inconclusive",
        reasoning=reason,
        abstain_reason=reason,
        suggested_strength="weak",
    )


def _mock_payload(
    *,
    settings: ModelSettings,
    finding: Finding,
    trigger: str | None,
) -> CrossVerificationPayload:
    """mock_llm=True 时的 deterministic 输出。

    设计目标：让 mock 也能演示出三种关键状态——
        - executor_blocked / unsupported_language -> supported（演示 fallback 走通）
        - 其它情况                                  -> inconclusive
    单测可以靠 trigger 直接验证 executor + board 的接线，不需要真模型。
    """

    if trigger in {"unsupported_language", "executor_blocked"}:
        return CrossVerificationPayload(
            layer="repro_agent",
            model=f"mock/{settings.provider}/{settings.model}",
            status="supported",
            reasoning="mock ReproAgent：trigger 命中，给出 supported 占位结论。",
            reproduction_steps=[
                f"在目标语言 {finding.file_path} 中复现 {finding.problem}",
            ],
            expected_observation="人工执行后应能观察到 finding 描述的现象。",
            suggested_strength="weak",
        )

    return CrossVerificationPayload(
        layer="repro_agent",
        model=f"mock/{settings.provider}/{settings.model}",
        status="inconclusive",
        reasoning="mock ReproAgent：trigger 不在 mock 命中范围，返回 inconclusive。",
        suggested_strength="weak",
    )
