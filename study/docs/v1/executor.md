# `codecross/executor.py` 解析

## 文件设计说明

`executor.py` 是确定性测试执行器。

它不调用 LLM，不判断风险，不更新 Board。它只负责：

```text
接收 TestCase
安全过滤
写临时 pytest 文件
真实运行 pytest
收集 stdout/stderr/exit_code
返回 TestResult
```

这样设计的原因是：

- LLM 不能口头宣称“测试通过”。
- CodeCross 必须拿到真实执行证据。
- 执行测试是高风险动作，需要安全过滤和 timeout。
- 测试执行结果要结构化，方便 Board 规则判断。

## 常量

### `BLOCKED_TEST_SNIPPETS`

一组危险代码片段。

包括：

- `subprocess`
- `os.system`
- `eval(`
- `exec(`
- `socket`
- `requests.`
- `urllib.`
- 文件删除/写入相关操作

目的：

```text
防止模型生成的测试执行 shell、访问网络或破坏文件系统。
```

V1 策略偏保守。

## 函数详解

### `is_safe_test_code(test_case, runner_config)`

对生成测试做安全过滤。

判断规则：

1. `test_case.safety_level == "unsafe"` 直接拦截。
2. 测试需要外部服务且 `allow_network=False`，拦截。
3. 测试代码包含危险片段，拦截。
4. 测试代码包含 blocked command，拦截。

返回：

```python
(True, "")
```

或：

```python
(False, "blocked_test_snippet:subprocess")
```

### `_summarize_failure(stdout, stderr)`

从 pytest 输出中提取关键失败摘要。

它会优先保留包含这些关键词的行：

- `FAILED`
- `ERROR`
- `AssertionError`
- `Exception`
- `Traceback`
- `E   `

这样最终报告不会塞满完整 pytest 日志。

### `_unsafe_result(test_case, round_index, reason)`

把被 sanitizer 拦截的测试转换成 `TestResult`。

状态是：

```text
unsafe
```

Board 会把这种结果视为：

```text
not_testable 或 coverage_gap
```

### `execute_pytest_case(test_case, project_root, round_index, runner_config)`

执行单个 pytest 测试。

这是 executor 最核心函数。

流程：

1. 调用 `is_safe_test_code`。
2. 如果不安全，返回 `_unsafe_result`。
3. 在项目根目录下创建 `.codecross-tmp`。
4. 创建临时 pytest 文件。
5. 设置 `PYTHONPATH` 为项目根目录。
6. 调用：

```python
subprocess.run(
    [sys.executable, "-m", "pytest", str(test_file), "-q"],
    cwd=root,
    capture_output=True,
    text=True,
    timeout=...,
    env=env,
)
```

7. 根据 returncode 判断结果：

```text
0 -> passed
1 -> failed
其他 -> error
timeout -> timeout
```

8. 返回 `TestResult`。

调试时重点看：

```python
test_case.test_code
completed.returncode
completed.stdout
completed.stderr
```

### `execute_test_cases(test_cases, project_root, round_index, runner_config)`

执行一批测试。

特点：

- 最多执行 `max_tests_per_round` 个。
- 只支持 `pytest` 类型。
- 非 pytest 类型会被 `skipped`。
- 每个测试都调用 `execute_pytest_case`。

## 临时文件策略

测试文件写到：

```text
项目根目录/.codecross-tmp/pytest-xxxx/test_xxx.py
```

不是写到真实 `tests/` 目录。

原因：

- 避免污染用户项目。
- 方便每轮隔离。
- TemporaryDirectory 会自动清理。

## 这个文件的关键思想

`executor.py` 是 CodeCross 的“现实检验层”。

Agent 可以提出怀疑，Test Case Agent 可以生成测试，但只有 executor 返回的 `TestResult` 才是真实执行证据。

