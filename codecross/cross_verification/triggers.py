"""LLM 交叉验证 Fallback 的触发器（Issue #2 Step 1）。

executor 在分发一个 ``TestCase`` 之前会先问一句：“这条测试现在 pytest 还跑得动吗？”
本模块只回答这个问题，不真正去跑模型。返回的字符串字面量就是
``TestCase.cross_verification_trigger`` 允许的取值之一，便于上层直接写回。

设计要点：
- 把判定规则集中在这里，executor 只需要 ``if trigger: ... else: ...``，
  改规则不用动 executor 关键路径。
- 触发器之间是“先到先得”的优先级关系：unsupported_language（语言层面就跑不动）
  优先于 environment_error（语言对，但环境塌了）；environment_error 优先于
  executor_blocked（沙箱级拦截）。这种顺序可以避免 fallback 在错误的层级反复触发。
- 任何不能稳定复现的判定都不放进来。例如“依赖 import 失败”这种属于
  test 自己的运行时错误，仍走原 pytest 失败路径，不进 fallback。
"""

from __future__ import annotations

import re
from typing import Literal

from ..config import RunnerConfig
from ..schemas import TestCase


# 表驱动：只有真正确定能跑 pytest 的语言才走 pytest 主路。
# 之所以用 frozenset：未来加 “go”、“rust” 类似的伪语言时，只在这里加一行，
# trigger 函数本体保持不变，方便 review。
_PYTEST_NATIVE_LANGUAGES: frozenset[str] = frozenset({"python"})

# 经验型 stderr 关键字：当 pytest 真的没装、或解释器无法找到 site-packages
# 时，subprocess 的 stderr 通常会落在下面这些片段中。匹配命中即认为
# 是“环境层面坏掉”，让 ReproAgent 接力，而不是把它误报成代码 finding。
_ENVIRONMENT_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"No module named ['\"]?pytest['\"]?", re.IGNORECASE),
    re.compile(r"pytest:\s*command not found", re.IGNORECASE),
    re.compile(r"ModuleNotFoundError:\s*No module named ['\"]?pytest['\"]?", re.IGNORECASE),
    re.compile(r"Could not find a version that satisfies the requirement pytest", re.IGNORECASE),
    # Windows 下解释器无 site-packages 时常见的崩溃签名：
    re.compile(r"ImportError:\s*No module named site", re.IGNORECASE),
)


CrossTrigger = Literal["unsupported_language", "environment_error", "executor_blocked"]


def detect_cross_verification_trigger(
    test_case: TestCase,
    runner_config: RunnerConfig,
    *,
    pytest_stderr: str | None = None,
    sanitizer_blocked: bool = False,
) -> CrossTrigger | None:
    """根据 test_case 与运行上下文决定是否需要 LLM 交叉验证。

    返回 None 表示走原有 pytest 路径；返回字面量则告诉 executor 改派给
    ReproAgent，并把这个字面量写进 :class:`TestCase.cross_verification_trigger`。

    参数：
        test_case: 准备执行的测试用例。``target_language`` 与 ``test_type``
            是关键判据；其它字段不会影响判定，避免引入额外耦合。
        runner_config: 运行配置。``cross_verification_enabled=False`` 时永远
            返回 None，保证开关可关。
        pytest_stderr: 可选，调用方一般在 pytest 失败之后再问一次触发器，
            把 stderr 传进来匹配“环境塌了”的关键字。Step 1 中 executor 只在
            预飞阶段调用，所以这个字段大多数情况是 None。
        sanitizer_blocked: 当测试代码被 AST sanitizer 判定不安全、又不该
            直接放弃这个 finding 时，executor 会把这个标志置 True 让 fallback
            接力。同样也是为了把决策点集中在这里，方便测试。
    """

    # 显式开关：fallback 默认开启，但允许通过配置一键关闭。
    if not runner_config.cross_verification_enabled:
        return None

    # 1) 语言不在 pytest 原生支持列表中。检查 target_language 而不是
    #    test_type，因为 test_type 是“测试种类”，target_language 才是
    #    “被测代码用什么语言写的”。
    language = (test_case.target_language or "").strip().lower()
    if language and language not in _PYTEST_NATIVE_LANGUAGES:
        return "unsupported_language"

    # 2) 环境错误：必须给到 stderr 才能判定，避免凭空臆测。
    if pytest_stderr:
        for pattern in _ENVIRONMENT_ERROR_PATTERNS:
            if pattern.search(pytest_stderr):
                return "environment_error"

    # 3) 沙箱拦截：sanitizer 把测试代码挡了下来，但 finding 依然需要回答。
    #    这条放最后，是因为它的证据强度最弱，应优先让前两条规则解释。
    if sanitizer_blocked:
        return "executor_blocked"

    return None
