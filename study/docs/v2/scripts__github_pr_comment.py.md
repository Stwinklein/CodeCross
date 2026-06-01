# `scripts/github_pr_comment.py` 解析

## 文件作用
GitHub PR 评论发布脚本，查找带隐藏 marker 的旧评论并执行 create/update。

- 源码行数：212
- 主要依赖：`argparse`、`json`、`os`、`sys`、`urllib`、`dataclasses`、`pathlib`、`typing`

## 函数与类

### `GitHubApiError`（类，第 28 行）
- 签名：`class GitHubApiError(RuntimeError)`
- 作用：Raised when GitHub returns a non-2xx response.

### `IssueComment`（类，第 33 行）
- 签名：`class IssueComment`
- 作用：Minimal shape of a GitHub issue comment used by the upsert logic.
- 主要字段/属性：
  - `comment_id: int`
  - `body: str`

### `GitHubIssueCommentClient`（类，第 40 行）
- 签名：`class GitHubIssueCommentClient`
- 作用：Tiny GitHub REST client for issue comments.
- 方法：
  - `__init__`（第 48 行）：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。
  - `_request`（第 53 行）：Send one JSON request to GitHub and decode the response.
  - `list_issue_comments`（第 78 行）：Fetch all top-level PR conversation comments.
  - `create_issue_comment`（第 99 行）：Create the first CodeCross comment on a PR.
  - `update_issue_comment`（第 109 行）：Replace the existing CodeCross comment body.

### `_read_pr_number_from_event`（函数，第 120 行）
- 签名：`def _read_pr_number_from_event(event_path: str | None) -> int | None`
- 作用：Infer the PR number from the GitHub Actions event payload.

### `find_existing_comment`（函数，第 134 行）
- 签名：`def find_existing_comment(comments: list[IssueComment], marker: str=DEFAULT_MARKER) -> IssueComment | None`
- 作用：Return the first existing CodeCross comment, if any.

### `upsert_comment`（函数，第 143 行）
- 签名：`def upsert_comment(*, client: GitHubIssueCommentClient, pr_number: int, body: str, marker: str=DEFAULT_MARKER) -> IssueComment`
- 作用：Create or update the stable CodeCross PR comment.

### `parse_args`（函数，第 158 行）
- 签名：`def parse_args(argv: list[str] | None=None) -> argparse.Namespace`
- 作用：Parse the GitHub comment command arguments.

### `main`（函数，第 175 行）
- 签名：`def main(argv: list[str] | None=None) -> None`
- 作用：CLI entry point used by the GitHub Actions workflow.

## 学习提示
先看文件作用和公开函数，再顺着调用关系回到核心 workflow，会更容易理解它在项目里的位置。
