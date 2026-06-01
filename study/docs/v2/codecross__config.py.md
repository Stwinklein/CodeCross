# `codecross/config.py` 解析

## 文件作用
运行配置层，从环境变量和默认值构造模型角色、reviewer、runner 安全预算与上下文检索范围。

- 源码行数：300
- 主要依赖：`os`、`dataclasses`、`pathlib`、`dotenv`

## 函数与类

### `ModelSettings`（类，第 20 行）
- 签名：`class ModelSettings`
- 作用：一个模型调用角色的配置。
- 主要字段/属性：
  - `role: str`
  - `provider: str`
  - `model: str`
  - `name: str | None = None`
  - `focus: str = ''`
  - `temperature: float = 0.0`
  - `max_tokens: int = 4000`
  - `timeout_seconds: int = 60`
  - `retry_count: int = 1`
  - `enabled: bool = True`
  - `api_key_env: str | None = None`
  - `base_url_env: str | None = None`
  - `api_key_override: str | None = None`
  - `base_url_override: str | None = None`
  - `a2a_url: str | None = None`
- 方法：
  - `display_name`（第 44 行）：报告中显示的名字；未显式设置时退回 role。
  - `api_key`（第 50 行）：从环境变量读取 API key，避免把密钥存到对象 repr 或源码里。
  - `base_url`（第 58 行）：从环境变量读取自定义网关地址。

### `RunnerConfig`（类，第 67 行）
- 签名：`class RunnerConfig`
- 作用：Evidence Verification Loop 和 pytest executor 的安全/预算配置。
- 主要字段/属性：
  - `max_verification_rounds: int = 3`
  - `max_tests_per_round: int = 8`
  - `test_timeout_seconds: int = 20`
  - `allow_network: bool = False`
  - `allow_write_outside_project: bool = False`
  - `fail_open: bool = True`
  - `blocked_commands: list[str] = field(default_factory=lambda: ['curl', 'wget', 'powershell', 'cmd.exe', 'bash', 'rm ', 'del ', 'Remove-Item'])`
  - `cross_verification_enabled: bool = True`
  - `cross_verification_max_per_round: int = 4`
  - `cross_verification_timeout_seconds: int = 60`

### `ProjectConfig`（类，第 99 行）
- 签名：`class ProjectConfig`
- 作用：整个 CodeCross 工作流需要的项目级配置。
- 主要字段/属性：
  - `language: str = 'python'`
  - `test_framework: str = 'pytest'`
  - `package_manager: str = 'uv'`
  - `source_roots: list[str] = field(default_factory=lambda: ['.'])`
  - `test_roots: list[str] = field(default_factory=lambda: ['tests'])`
  - `entry_points: list[str] = field(default_factory=list)`
  - `allowed_commands: list[str] = field(default_factory=lambda: ['python -m pytest'])`
  - `model_config: dict[str, ModelSettings] = field(default_factory=dict)`
  - `reviewers: list[ModelSettings] = field(default_factory=list)`
  - `runner_config: RunnerConfig = field(default_factory=RunnerConfig)`
  - `mock_llm: bool = False`
- 方法：
  - `enabled_reviewers`（第 114 行）：返回启用的 reviewer 列表，workflow 会据此动态创建 reviewer 节点。

### `_load_env`（函数，第 120 行）
- 签名：`def _load_env(project_root: Path, *, override: bool=False) -> None`
- 作用：加载项目根目录 .env，并兼容 langchain-deepseek 使用的变量名。

### `load_project_config`（函数，第 130 行）
- 签名：`def load_project_config(project_root: str | Path | None=None, *, mock_llm: bool=False) -> ProjectConfig`
- 作用：构造 V1 默认配置。现在会额外注册 `report_localizer` 模型角色，默认走 DeepSeek；可通过 `CODECROSS_REPORT_LOCALIZER_PROVIDER`、`CODECROSS_REPORT_LOCALIZER_MODEL`、`CODECROSS_REPORT_LOCALIZER_API_KEY_ENV`、`CODECROSS_REPORT_LOCALIZER_BASE_URL_ENV` 切换到其他模型渠道。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
