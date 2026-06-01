# `tests/test_board.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `board` 相关行为和回归场景。

- 源码行数：154
- 主要依赖：`codecross`

## 函数与类

### `test_board_marks_failed_related_test_as_supported`（函数，第 18 行）
- 签名：`def test_board_marks_failed_related_test_as_supported()`
- 作用：相关 pytest 失败时，FindingVerification 应从 unverified 变成 supported。

### `test_board_keeps_non_reproducible_failure_inconclusive`（函数，第 82 行）
- 签名：`def test_board_keeps_non_reproducible_failure_inconclusive()`
- 作用：失败测试没有复现标记时，只能算弱信号，不能直接 supported。

### `test_board_stops_when_round_has_no_verification_progress`（函数，第 119 行）
- 签名：`def test_board_stops_when_round_has_no_verification_progress()`
- 作用：验证队列仍有目标但本轮无状态变化时，应提前停止，避免重复空转。

### `test_board_markdown_is_english_source_report`（函数，第 133 行）
- 签名：`def test_board_markdown_is_english_source_report()`
- 作用：The source final report is English before the localizer agent translates it.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
