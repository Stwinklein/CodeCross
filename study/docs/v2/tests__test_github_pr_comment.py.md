# `tests/test_github_pr_comment.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `github / pr / comment` 相关行为和回归场景。

- 源码行数：82
- 主要依赖：`scripts`

## 函数与类

### `FakeCommentClient`（类，第 13 行）
- 签名：`class FakeCommentClient`
- 作用：In-memory stand-in for GitHubIssueCommentClient.
- 方法：
  - `__init__`（第 16 行）：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。
  - `list_issue_comments`（第 21 行）：Return preloaded comments and record no network state.
  - `update_issue_comment`（第 26 行）：Record update calls so the test can assert the upsert path.
  - `create_issue_comment`（第 32 行）：Record create calls so the test can assert the upsert path.

### `test_find_existing_comment_uses_marker`（函数，第 39 行）
- 签名：`def test_find_existing_comment_uses_marker()`
- 作用：Only comments with the hidden CodeCross marker should be updated.

### `test_upsert_comment_updates_existing_marker_comment`（函数，第 53 行）
- 签名：`def test_upsert_comment_updates_existing_marker_comment()`
- 作用：A synchronize event should patch the old bot comment, not add noise.

### `test_upsert_comment_creates_when_marker_is_missing`（函数，第 69 行）
- 签名：`def test_upsert_comment_creates_when_marker_is_missing()`
- 作用：The first CodeCross run on a PR should create a new comment.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
