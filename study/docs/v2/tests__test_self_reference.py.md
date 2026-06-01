# `tests/test_self_reference.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `self / reference` 相关行为和回归场景。

- 源码行数：182
- 主要依赖：`codecross`

## 函数与类

### `_make_high_supported_board`（函数，第 29 行）
- 签名：`def _make_high_supported_board(finding_id: str) -> BoardSummary`
- 作用：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。

### `_make_high_finding`（函数，第 38 行）
- 签名：`def _make_high_finding(file_path: str='.github/workflows/codecross-pr-review.yml') -> Finding`
- 作用：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。

### `test_is_self_referential_review_detects_workflow_only_diff`（函数，第 54 行）
- 签名：`def test_is_self_referential_review_detects_workflow_only_diff()`
- 作用：pytest 用例，验证 `is self referential review detects workflow only diff` 这个行为或回归场景。

### `test_is_self_referential_review_detects_runner_directory`（函数，第 59 行）
- 签名：`def test_is_self_referential_review_detects_runner_directory()`
- 作用：pytest 用例，验证 `is self referential review detects runner directory` 这个行为或回归场景。

### `test_is_self_referential_review_rejects_mixed_diff`（函数，第 64 行）
- 签名：`def test_is_self_referential_review_rejects_mixed_diff()`
- 作用：pytest 用例，验证 `is self referential review rejects mixed diff` 这个行为或回归场景。

### `test_is_self_referential_review_rejects_empty_changed_files`（函数，第 74 行）
- 签名：`def test_is_self_referential_review_rejects_empty_changed_files()`
- 作用：pytest 用例，验证 `is self referential review rejects empty changed files` 这个行为或回归场景。

### `test_evaluator_downgrades_high_to_medium_on_self_reference`（函数，第 79 行）
- 签名：`def test_evaluator_downgrades_high_to_medium_on_self_reference()`
- 作用：pytest 用例，验证 `evaluator downgrades high to medium on self reference` 这个行为或回归场景。

### `test_evaluator_keeps_high_when_diff_is_not_self_reference`（函数，第 98 行）
- 签名：`def test_evaluator_keeps_high_when_diff_is_not_self_reference()`
- 作用：pytest 用例，验证 `evaluator keeps high when diff is not self reference` 这个行为或回归场景。

### `test_evaluator_without_review_plan_behaves_like_before`（函数，第 115 行）
- 签名：`def test_evaluator_without_review_plan_behaves_like_before()`
- 作用：pytest 用例，验证 `evaluator without review plan behaves like before` 这个行为或回归场景。

### `test_final_report_appends_self_reference_notice`（函数，第 130 行）
- 签名：`def test_final_report_appends_self_reference_notice()`
- 作用：pytest 用例，验证 `final report appends self reference notice` 这个行为或回归场景。

### `test_final_report_omits_self_reference_notice_for_normal_pr`（函数，第 165 行）
- 签名：`def test_final_report_omits_self_reference_notice_for_normal_pr()`
- 作用：pytest 用例，验证 `final report omits self reference notice for normal pr` 这个行为或回归场景。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
