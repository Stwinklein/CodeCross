# `codecross/a2a.py` 解析

## 文件作用
A2A reviewer 客户端适配层，负责 HTTP POST、响应解析、错误兜底和 `ReviewerResult` 规范化。

- 源码行数：110
- 主要依赖：`json`、`urllib`、`typing`、`uuid`、`config`、`context`、`llm`、`schemas`

## 函数与类

### `_http_post_json`（函数，第 24 行）
- 签名：`def _http_post_json(url: str, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]`
- 作用：POST JSON with stdlib urllib to avoid adding a new runtime dependency.

### `_extract_reviewer_result`（函数，第 40 行）
- 签名：`def _extract_reviewer_result(response: dict[str, Any]) -> ReviewerResult`
- 作用：Extract ReviewerResult from a minimal A2A JSON-RPC response.

### `call_a2a_reviewer`（函数，第 59 行）
- 签名：`def call_a2a_reviewer(*, reviewer: ModelSettings, diff: str, review_plan: ReviewPlan, code_context: CodeContext | None, mock_llm: bool) -> ReviewerResult`
- 作用：Call a remote A2A reviewer and normalize the returned reviewer result.

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
