"""LLM 交叉验证 Fallback（Issue #2）。

当 pytest 无法运行时（例如目标语言不是 Python、运行环境损坏，或测试代码被
sanitizer 拦下），CodeCross 不再直接给 ``not_testable`` 了事，而是改走一条
LLM 交叉验证流水线：

    Layer 1 ReproAgent      - 让独立模型尝试给出复现步骤或反驳依据
    Layer 2 RefuteAgent     - （后续阶段）对 Repro 结论做反向核查
    Layer 3 Behavior Oracle - （后续阶段）针对接口契约做行为模拟
    Layer 4 Human Escape    - 最终仍冲突 -> needs_human_review

本目录只暴露 ReproAgent 与触发器（Step 1 + Step 2）。后续的 Refute / Oracle
会以同名子模块加入，复用同一份 :class:`CrossVerificationPayload`。
"""

from .triggers import detect_cross_verification_trigger
from .repro_agent import run_repro_agent

__all__ = [
    "detect_cross_verification_trigger",
    "run_repro_agent",
]
