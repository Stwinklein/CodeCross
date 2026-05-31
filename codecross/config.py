from __future__ import annotations

"""CodeCross 的运行配置。

V1 暂时不引入 YAML/TOML 配置文件，而是用 dataclass + .env 组合。
这样一眼能看到系统默认有哪些角色、每个角色使用哪个模型渠道。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


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
    """Evidence Verification Loop 和 pytest executor 的安全/预算配置。"""

    max_verification_rounds: int = 3
    max_tests_per_round: int = 8
    test_timeout_seconds: int = 20
    allow_network: bool = False
    allow_write_outside_project: bool = False
    fail_open: bool = True
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


@dataclass(slots=True)
class ProjectConfig:
    """整个 CodeCross 工作流需要的项目级配置。"""

    language: str = "python"
    test_framework: str = "pytest"
    package_manager: str = "uv"
    source_roots: list[str] = field(default_factory=lambda: ["."])
    test_roots: list[str] = field(default_factory=lambda: ["tests"])
    entry_points: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=lambda: ["python -m pytest"])
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
) -> ProjectConfig:
    """构造 V1 默认配置。

    这里把 .env 中的 DeepSeek 和 Anthropic 两路模型映射成两个独立 reviewer：
    - logic_reviewer: 更偏逻辑、边界和运行时问题
    - security_maintainability_reviewer: 更偏安全、接口设计和测试遗漏
    """

    root = Path(project_root).resolve() if project_root else PROJECT_ROOT
    _load_env(root, override=project_root is not None)

    deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    deepseek = ModelSettings(
        role="deepseek_reviewer",
        name="logic_reviewer",
        provider="deepseek",
        model=deepseek_model,
        focus="logic, boundary conditions, runtime exceptions, maintainability",
        temperature=0.2,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
    )
    second = ModelSettings(
        role="second_reviewer",
        name="security_maintainability_reviewer",
        provider="anthropic",
        model=anthropic_model,
        focus="security, API design, failure modes, missing tests",
        temperature=0.2,
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
    report_formatter = ModelSettings(
        role="report_formatter",
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

    return ProjectConfig(
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
            "report_formatter": report_formatter,
            "high_risk_reviewer": high_risk,
        },
        mock_llm=mock_llm,
    )
