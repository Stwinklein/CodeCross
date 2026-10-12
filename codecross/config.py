from __future__ import annotations

"""CodeCross 的运行配置。

V1 暂时不引入 YAML/TOML 配置文件，而是用 dataclass + .env 组合。
这样一眼能看到系统默认有哪些角色、每个角色使用哪个模型渠道。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from .project_detection import detect_project


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class ModelSettings:
    """一个模型调用角色的配置。

    同一个模型 provider 可以承担多个 role，例如 DeepSeek 同时做 planner、
    test_case_agent 和 evaluator；reviewer 则保留独立的 name/focus。
    """

    role: str
    provider: str
    model: str
    name: str | None = None
    focus: str = ""
    temperature: float = 0.0
    max_tokens: int = 4000
    timeout_seconds: int = 60
    retry_count: int = 1
    enabled: bool = True
    api_key_env: str | None = None
    base_url_env: str | None = None
    api_key_override: str | None = None
    base_url_override: str | None = None
    a2a_url: str | None = None

    @property
    def display_name(self) -> str:
        """报告中显示的名字；未显式设置时退回 role。"""

        return self.name or self.role

    @property
    def api_key(self) -> str | None:
        """从环境变量读取 API key，避免把密钥存到对象 repr 或源码里。"""

        if self.api_key_override:
            return self.api_key_override
        return os.getenv(self.api_key_env) if self.api_key_env else None

    @property
    def base_url(self) -> str | None:
        """从环境变量读取自定义网关地址。"""

        if self.base_url_override:
            return self.base_url_override
        return os.getenv(self.base_url_env) if self.base_url_env else None


@dataclass(slots=True)
class RunnerConfig:
    """Evidence Verification Loop 和多语言 executor 的安全/预算配置。"""

    max_verification_rounds: int = 3
    max_tests_per_round: int = 8
    test_timeout_seconds: int = 20
    # AI 生成测试默认只能进入容器；trusted_local 仅供可信代码的显式本地调试。
    execution_mode: Literal["docker", "trusted_local"] = "docker"
    sandbox_memory_mb: int = 512
    sandbox_cpu_count: float = 1.0
    sandbox_pids_limit: int = 64
    sandbox_output_limit_bytes: int = 1_000_000
    allow_network: bool = False
    allow_write_outside_project: bool = False
    fail_open: bool = True
    # 用户显式覆盖测试命令时保存已经拆分好的 argv。
    # executor 始终使用 subprocess.run(..., shell=False)，因此这里不能保存
    # `npm test && curl ...` 之类的 shell 表达式；CLI 会在进入配置前完成安全拆分。
    test_command_override: list[str] | None = None
    blocked_commands: list[str] = field(
        default_factory=lambda: [
            "curl",
            "wget",
            "powershell",
            "cmd.exe",
            "bash",
            "rm ",
            "del ",
            "Remove-Item",
        ]
    )
    # Issue #2：LLM 交叉验证 Fallback 的预算。
    # - enabled=False 时整条 fallback 路径关闭，executor 仍然返回 not_testable，
    #   行为与历史一致；这样新功能可以灰度开关，避免一次性放出来。
    # - max_per_round 限制单轮最多调用多少次 ReproAgent（每次都要花模型预算）。
    # - timeout_seconds 单独于 pytest 的 test_timeout_seconds，因为模型调用通常更慢。
    cross_verification_enabled: bool = True
    cross_verification_max_per_round: int = 4
    cross_verification_timeout_seconds: int = 60


@dataclass(slots=True)
class ProjectConfig:
    """整个 CodeCross 工作流需要的项目级配置。"""

    language: str = "python"
    test_framework: str = "pytest"
    package_manager: str = "uv"
    source_roots: list[str] = field(default_factory=lambda: ["."])
    test_roots: list[str] = field(default_factory=lambda: ["tests"])
    entry_points: list[str] = field(default_factory=list)
    # ProjectDetector 给出的结构化 argv。allowed_commands 暂时保留字符串形式，
    # 兼容已有报告/API；真正执行必须使用本字段，避免运行时再次猜测如何分词。
    test_command: list[str] = field(default_factory=lambda: ["python", "-m", "pytest"])
    allowed_commands: list[str] = field(default_factory=lambda: ["python -m pytest"])
    # 两种模式共用 Planner、上下文和 Reviewer；review_only 只跳过后置证据环。
    evaluation_mode: Literal["review_only", "evidence_verification"] = "evidence_verification"
    detection_confidence: str = "low"
    detection_evidence: list[str] = field(default_factory=list)
    semantic_deduplication_enabled: bool = False
    finding_critic_enabled: bool = False
    max_final_findings: int = 5
    # Prompt 版本进入每次评测输出和 LangSmith metadata。修改 Prompt 时必须显式
    # 提升该值，避免两个行为不同的实验在平台上显示成同一版本。
    prompt_version: str = "v0.25"
    review_profile: Literal["defect_detection", "human_review_alignment"] = "defect_detection"
    model_config: dict[str, ModelSettings] = field(default_factory=dict)
    reviewers: list[ModelSettings] = field(default_factory=list)
    runner_config: RunnerConfig = field(default_factory=RunnerConfig)
    mock_llm: bool = False

    def enabled_reviewers(self) -> list[ModelSettings]:
        """返回启用的 reviewer 列表，workflow 会据此动态创建 reviewer 节点。"""

        return [reviewer for reviewer in self.reviewers if reviewer.enabled]


def _load_env(project_root: Path, *, override: bool = False) -> None:
    """加载项目根目录 .env，并兼容 langchain-deepseek 使用的变量名。"""

    # API 显式传入 project_root 时，项目 .env 应该优先，避免旧终端环境变量
    # 把用户刚写进 .env 的模型名盖掉；普通库调用仍默认尊重当前进程环境。
    load_dotenv(project_root / ".env", override=override)
    if os.getenv("DEEPSEEK_BASE_URL") and not os.getenv("DEEPSEEK_API_BASE"):
        os.environ["DEEPSEEK_API_BASE"] = os.environ["DEEPSEEK_BASE_URL"]


def load_project_config(
    project_root: str | Path | None = None,
    *,
    mock_llm: bool = False,
    diff: str = "",
    evaluation_mode: Literal["review_only", "evidence_verification"] = "evidence_verification",
    semantic_deduplication: bool | None = None,
    review_profile: Literal["defect_detection", "human_review_alignment"] = "defect_detection",
    test_command_override: list[str] | None = None,
) -> ProjectConfig:
    """构造 V1 默认配置。

    这里把 .env 中的 DeepSeek 和 Anthropic 两路模型映射成两个独立 reviewer：
    - logic_reviewer: 更偏逻辑、边界和运行时问题
    - security_maintainability_reviewer: 更偏安全、接口设计和测试遗漏
    """

    root = Path(project_root).resolve() if project_root else PROJECT_ROOT
    # Capture this before reading the target project's .env: source being reviewed
    # must not be able to opt itself into executing generated tests on the host.
    host_execution_mode = os.environ.get("CODECROSS_TEST_EXECUTION_MODE")
    execution_mode = (host_execution_mode or "docker").strip().lower()
    if execution_mode not in {"docker", "trusted_local"}:
        raise ValueError("CODECROSS_TEST_EXECUTION_MODE must be docker or trusted_local")
    _load_env(root, override=project_root is not None)
    if host_execution_mode is None:
        os.environ.pop("CODECROSS_TEST_EXECUTION_MODE", None)
    else:
        os.environ["CODECROSS_TEST_EXECUTION_MODE"] = host_execution_mode
    profile = detect_project(root, diff=diff)

    deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
    # 公开 benchmark 需要可复现的版本比较；产品交互保留少量温度以增加探索面。
    reviewer_temperature = 0.0 if review_profile == "human_review_alignment" else 0.2
    report_localizer_provider = os.getenv(
        "CODECROSS_REPORT_LOCALIZER_PROVIDER", "deepseek"
    ).strip().lower()
    default_localizer_model = (
        deepseek_model if report_localizer_provider == "deepseek" else anthropic_model
    )
    report_localizer_model = os.getenv("CODECROSS_REPORT_LOCALIZER_MODEL", default_localizer_model)
    report_localizer_api_key_env = os.getenv(
        "CODECROSS_REPORT_LOCALIZER_API_KEY_ENV",
        "DEEPSEEK_API_KEY" if report_localizer_provider == "deepseek" else "ANTHROPIC_API_KEY",
    )
    report_localizer_base_url_env = os.getenv(
        "CODECROSS_REPORT_LOCALIZER_BASE_URL_ENV",
        "DEEPSEEK_BASE_URL" if report_localizer_provider == "deepseek" else "ANTHROPIC_BASE_URL",
    )

    # 产品默认保留 DeepSeek + Anthropic 的跨模型互补；公开 human-alignment 评测更关注
    # Reviewer 能力上限，默认让 correctness pass 也使用已验证的 Anthropic 模型，但仍
    # 使用独立 Prompt/独立调用。可通过环境变量切回 deepseek，便于做成本与消融对照。
    human_primary_provider = os.getenv(
        "CODECROSS_HUMAN_ALIGNMENT_PRIMARY_PROVIDER", "anthropic"
    ).strip().lower()
    if human_primary_provider not in {"anthropic", "deepseek"}:
        human_primary_provider = "anthropic"
    primary_uses_anthropic = (
        review_profile == "human_review_alignment"
        and human_primary_provider == "anthropic"
    )
    deepseek = ModelSettings(
        role="anthropic_logic_reviewer" if primary_uses_anthropic else "deepseek_reviewer",
        name="logic_reviewer",
        provider="anthropic" if primary_uses_anthropic else "deepseek",
        model=anthropic_model if primary_uses_anthropic else deepseek_model,
        focus="logic, boundary conditions, runtime exceptions, maintainability",
        temperature=reviewer_temperature,
        timeout_seconds=120,
        retry_count=2,
        api_key_env="ANTHROPIC_API_KEY" if primary_uses_anthropic else "DEEPSEEK_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL" if primary_uses_anthropic else "DEEPSEEK_BASE_URL",
    )
    second = ModelSettings(
        role="second_reviewer",
        name="security_maintainability_reviewer",
        provider="anthropic",
        model=anthropic_model,
        # 第二路不再重复第一路的 bug hunt，而是承担维护者视角。双 Reviewer 只有
        # 真正覆盖不同关注面，交叉审核才可能提升召回而不是生成两份同义评论。
        focus=(
            "repository conventions, API and option naming, PR scope, documentation and release "
            "wording, test organization, UI/UX regressions, and maintainability"
        ),
        temperature=reviewer_temperature,
        timeout_seconds=120,
        retry_count=2,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
    )

    planner = ModelSettings(
        role="planner",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.0,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    strategy = ModelSettings(
        role="test_strategy_agent",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.0,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    case_generator = ModelSettings(
        role="test_case_agent",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.1,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    test_author = ModelSettings(
        role="test_author_agent",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.0,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    result_analyzer = ModelSettings(
        role="test_result_analyzer",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.0,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    coverage_gap = ModelSettings(
        role="coverage_gap_agent",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.0,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    targeted = ModelSettings(
        role="targeted_reviewer",
        provider="anthropic",
        model=anthropic_model,
        focus="targeted re-check for inconclusive evidence and conflicts",
        temperature=0.0,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
    )
    evaluator = ModelSettings(
        role="evaluator",
        provider="deepseek",
        model=deepseek_model,
        temperature=0.0,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    high_risk = ModelSettings(
        role="high_risk_reviewer",
        provider="anthropic",
        model=anthropic_model,
        temperature=0.0,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
    )
    # The final report is now written in English first, then localized by a
    # dedicated agent. It defaults to the existing DeepSeek channel, while these
    # CODECROSS_REPORT_LOCALIZER_* env vars let CI or the API route it elsewhere.
    report_localizer = ModelSettings(
        role="report_localizer",
        provider=report_localizer_provider,
        model=report_localizer_model,
        temperature=0.0,
        max_tokens=8000,
        api_key_env=report_localizer_api_key_env,
        base_url_env=report_localizer_base_url_env,
    )
    # Issue #2：LLM 交叉验证 ReproAgent 的角色配置。
    # 默认走 Anthropic：reviewer 已经是 DeepSeek + Anthropic 的组合，
    # ReproAgent 选 Anthropic 是为了在 fallback 路径上仍然保持“跨家族”独立性，
    # 避免同一个 deepseek 模型既出 finding 又自我确认。
    # 用户可以通过更换 ANTHROPIC_MODEL 或在 .env 里把 base_url 指到自家网关来切换。
    repro_agent = ModelSettings(
        role="repro_agent",
        provider="anthropic",
        model=anthropic_model,
        focus="cross-verification of pytest-unavailable findings",
        temperature=0.0,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
    )
    finding_deduplicator = ModelSettings(
        role="finding_deduplicator",
        provider="anthropic",
        model=anthropic_model,
        temperature=0.0,
        max_tokens=1200,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
    )
    finding_critic = ModelSettings(
        role="finding_critic",
        provider="anthropic",
        model=anthropic_model,
        temperature=0.0,
        max_tokens=4000,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
    )

    return ProjectConfig(
        language=profile.language,
        test_framework=profile.test_framework,
        package_manager=profile.package_manager,
        source_roots=profile.source_roots,
        test_roots=profile.test_roots,
        test_command=list(profile.test_command),
        allowed_commands=[" ".join(profile.test_command)] if profile.test_command else [],
        evaluation_mode=evaluation_mode,
        detection_confidence=profile.confidence,
        detection_evidence=profile.evidence,
        semantic_deduplication_enabled=(
            # human-alignment 保留不同 Reviewer 的具体措辞与抽象层级，交给官方
            # one-to-one Judge 判断；语义聚类曾把近似 human concern 合并进高严重度
            # 推测，造成不可逆 recall 损失。产品 defect 模式仍保留语义去重。
            evaluation_mode == "review_only" and review_profile == "defect_detection"
            if semantic_deduplication is None
            else semantic_deduplication
        ),
        # 产品 defect 模式需要 Critic 压制事实错误；SWE human-alignment 协议则由
        # 官方 CONFIRMED/PLAUSIBLE/FABRICATED Judge 计 FPR。若两边都过滤，会在
        # 评分前静默删除可命中的人类 concern，造成不可审计的 recall 损失。
        finding_critic_enabled=(
            evaluation_mode == "review_only" and review_profile == "defect_detection"
        ),
        review_profile=review_profile,
        max_final_findings=6 if review_profile == "human_review_alignment" else 5,
        prompt_version=os.getenv("CODECROSS_PROMPT_VERSION", "v0.25"),
        runner_config=RunnerConfig(
            execution_mode=execution_mode,  # type: ignore[arg-type]
            # 复制 argv，防止调用方后续修改原 list 时悄悄改变正在运行的配置。
            test_command_override=(
                list(test_command_override) if test_command_override else None
            )
        ),
        reviewers=[deepseek, second],
        model_config={
            "planner": planner,
            "test_strategy_agent": strategy,
            "test_case_agent": case_generator,
            "test_author_agent": test_author,
            "test_result_analyzer": result_analyzer,
            "coverage_gap_agent": coverage_gap,
            "targeted_reviewer": targeted,
            "evaluator": evaluator,
            "high_risk_reviewer": high_risk,
            "report_localizer": report_localizer,
            "repro_agent": repro_agent,
            "finding_deduplicator": finding_deduplicator,
            "finding_critic": finding_critic,
        },
        mock_llm=mock_llm,
    )
