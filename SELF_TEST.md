# CodeCross V1 自测指南

## 1. 快速单测

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

预期结果：

```text
5 passed
```

这组测试会验证：

- `.env` 两个模型渠道会被映射为 `logic_reviewer` 和 `security_maintainability_reviewer`
- pytest executor 会真实执行生成的测试
- Review Board 会把失败测试转换成 finding evidence
- LangGraph 主流程能跑通 V1 demo

## 2. 无模型端到端自测

这条命令不会调用真实模型，适合先验证本地流程：

```powershell
.\.venv\Scripts\python.exe -m codecross.main --demo --mock-llm --project-root . --output codecross_report.md
```

预期现象：

- 控制台打印 `CodeCross V1 Report`
- `Risk Level` 为 `high`
- Review Board 显示 `Reviewers: 2`
- Review Board 显示至少 1 个失败测试
- 根目录生成 `codecross_report.md`

也可以输出结构化 JSON：

```powershell
.\.venv\Scripts\python.exe -m codecross.main --demo --mock-llm --project-root . --json --output codecross_report.json
```

## 3. 真实模型渠道自测

`.env` 需要包含：

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL

ANTHROPIC_API_KEY
ANTHROPIC_BASE_URL
ANTHROPIC_MODEL
```

真实调用两个 reviewer：

```powershell
.\.venv\Scripts\python.exe -m codecross.main --demo --project-root . --output codecross_real_report.md
```

V1 默认 reviewer 配置：

- `logic_reviewer`: DeepSeek 渠道，关注逻辑 bug、边界条件、运行时异常、可维护性
- `security_maintainability_reviewer`: Anthropic 渠道，关注安全、API 设计、失败路径、测试遗漏

## 4. 审查自己的 diff

```powershell
.\.venv\Scripts\python.exe -m codecross.main --diff path\to\change.diff --project-root . --output my_codecross_report.md
```

如果只是想粘贴一小段 diff：

```powershell
.\.venv\Scripts\python.exe -m codecross.main --diff-text "diff --git a/foo.py b/foo.py ..." --project-root .
```

## 5. 当前 V1 范围

V1 已实现设计文档中的第一版主链路：

- Planner
- 两个独立 reviewer fan-out / fan-in
- Initial Review Board
- Board-driven Evidence Verification Loop
- pytest Deterministic Executor
- Targeted Reviewer 入口
- Rule-first Evaluator
- High Risk Review

`--mock-llm` 用于稳定自测；去掉该参数后才会调用 `.env` 中配置的真实模型渠道。
