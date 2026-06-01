# `codecross/ci.py` 解析

## 文件作用
CI/GitHub Actions 入口，读取 PR diff、运行 workflow，并生成可稳定更新的双语 PR 评论。

- 源码行数：349
- 主要依赖：`argparse`、`json`、`os`、`dataclasses`、`pathlib`、`typing`、`pydantic`、`config`、`schemas`、`workflow`

## 函数与类

### `PullRequestMetadata`（类，第 33 行）
- 签名：`class PullRequestMetadata`
- 作用：Small set of GitHub fields that make the PR comment self-describing.
- 主要字段/属性：
  - `repository: str = ''`
  - `pr_number: int | None = None`
  - `head_sha: str = ''`
  - `base_ref: str = ''`
  - `head_ref: str = ''`
  - `workflow_url: str = ''`

### `_env_bool`（函数，第 44 行）
- 签名：`def _env_bool(name: str, default: bool=False) -> bool`
- 作用：Parse common CI boolean spellings from environment variables.

### `_jsonable`（函数，第 53 行）
- 签名：`def _jsonable(value: Any) -> Any`
- 作用：Convert workflow state objects into JSON-safe primitives.

### `load_github_metadata`（函数，第 72 行）
- 签名：`def load_github_metadata(event_path: str | None=None) -> PullRequestMetadata`
- 作用：Load PR metadata from the GitHub Actions event payload.

### `_workflow_url`（函数，第 104 行）
- 签名：`def _workflow_url() -> str`
- 作用：Build a clickable Actions run URL when GitHub exposes enough env vars.

### `_model_label_from_state`（函数，第 115 行）
- 签名：`def _model_label_from_state(state: CodeCrossState) -> str`
- 作用：汇总 reviewer 使用的模型名称，但绝不展示 API key 或 base URL。

### `_reviewer_error_block`（函数，第 123 行）
- 签名：`def _reviewer_error_block(state: CodeCrossState) -> str`
- 作用：展示模型调用或传输错误，但不泄露 provider 原始错误细节。

### `truncate_comment`（函数，第 138 行）
- 签名：`def truncate_comment(markdown: str, max_chars: int) -> str`
- 作用：限制评论长度，同时保留用于更新旧评论的 HTML marker。

### `build_pr_comment`（函数，第 152 行）
- 签名：`def build_pr_comment(state: CodeCrossState, *, metadata: PullRequestMetadata | None=None, mock_llm: bool=False, max_chars: int=DEFAULT_MAX_COMMENT_CHARS) -> str`
- 作用：生成会被贴到 PR Conversation 的双语 Markdown 评论；它不再重建中文摘要，而是直接发布 workflow 中的 `final_report_en` 与 `final_report_zh`。

### `_required_settings_for_ci`（函数，第 223 行）
- 签名：`def _required_settings_for_ci(config: ProjectConfig) -> list[ModelSettings]`
- 作用：Return model roles that CI should verify before starting.

### `validate_model_credentials`（函数，第 251 行）
- 签名：`def validate_model_credentials(config: ProjectConfig) -> None`
- 作用：Fail fast when real CI review is requested without model credentials.

### `run_ci_review`（函数，第 267 行）
- 签名：`def run_ci_review(*, diff: str, project_root: Path, mock_llm: bool, require_models: bool) -> CodeCrossState`
- 作用：Load config and run the normal CodeCross workflow for CI.

### `read_diff_file`（函数，第 282 行）
- 签名：`def read_diff_file(path: str | Path) -> str`
- 作用：Read a unified diff and tolerate a UTF-8 BOM.

### `parse_args`（函数，第 294 行）
- 签名：`def parse_args(argv: list[str] | None=None) -> argparse.Namespace`
- 作用：Define the small CI command-line surface.

### `main`（函数，第 319 行）
- 签名：`def main(argv: list[str] | None=None) -> None`
- 作用：CLI entry point used by GitHub Actions.

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
