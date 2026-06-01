# `a2aproject/server.py` 解析

## 文件作用
最小 FastAPI A2A Reviewer 服务：接收 JSON-RPC 风格请求，把 diff 转成 CodeCross 的 `ReviewerResult`，用于演示远程 reviewer 接入。

- 源码行数：190
- 主要依赖：`typing`、`fastapi`、`codecross`

## 函数与类

### `_extract_data_payload`（函数，第 25 行）
- 签名：`def _extract_data_payload(request: dict[str, Any]) -> dict[str, Any]`
- 作用：Read the first data part from an A2A-style `message/send` request.

### `_deterministic_review`（函数，第 36 行）
- 签名：`def _deterministic_review(payload: dict[str, Any]) -> ReviewerResult`
- 作用：Local deterministic reviewer used when no model key is supplied.

### `build_reviewer_result`（函数，第 97 行）
- 签名：`def build_reviewer_result(payload: dict[str, Any]) -> ReviewerResult`
- 作用：Build ReviewerResult from A2A payload.

### `agent_card`（函数，第 140 行）
- 签名：`def agent_card() -> dict[str, Any]`
- 作用：Expose minimal discovery metadata for A2A clients.

### `a2a_rpc`（函数，第 159 行）
- 签名：`def a2a_rpc(request: dict[str, Any]) -> dict[str, Any]`
- 作用：Handle minimal A2A JSON-RPC `message/send` calls.

## 学习提示
先看文件作用和公开函数，再顺着调用关系回到核心 workflow，会更容易理解它在项目里的位置。
