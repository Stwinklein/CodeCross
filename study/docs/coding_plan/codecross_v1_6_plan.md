# CodeCross V1.6 计划：Test Author Agent

## 版本目标

V1.1-V1.5 已经把审查、上下文、Web Run、事件、终止和历史补齐。当前测试生成仍偏“把 finding 直接转成一个 pytest”，缺少真正测试作者会做的第二步：检查生成测试是否足够窄、是否有正常路径对照、是否会因为测试本身太脆而误导 Board。

V1.6 的目标是新增 Test Author Agent，让测试证据更像人工写出来的最小测试包。

## 核心改动

### 1. 新增 Test Author Agent

在 `codecross/agents.py` 增加：

```python
test_author_agent(...)
```

输入：

- 当前 round index
- verification targets
- test intents
- Test Case Agent 生成的 draft test cases
- ProjectConfig

输出：

- `TestCaseBatch`

职责：

- 保留安全的 draft pytest。
- 为高风险行为测试补一个正常路径对照测试，减少“函数完全坏掉”和“只在边界坏掉”混在一起。
- 对无法安全自动化的 finding 保持 manual/review_required，不强行生成危险测试。
- 继续遵守 executor 的 AST sandbox 限制。

### 2. Workflow 新增节点

在 `codecross/workflow.py` 中加入节点：

```text
test_case -> test_author -> executor
```

这样测试链路变成：

```text
Test Strategy
  -> Test Case Agent 生成草稿
  -> Test Author Agent 审校/补强
  -> Executor 执行最终测试包
```

### 3. Mock LLM 确定性行为

为了测试稳定，mock 模式先实现 `safe_divide/divide` 的正常路径对照：

```python
assert safe_divide(10, 2) == 5
```

配合已有零分母失败测试：

```python
assert safe_divide(10, 0) is None
```

Board 会同时看到：

- 边界路径失败：支持 finding。
- 正常路径通过：说明问题更集中，不是测试环境或导入整体坏掉。

### 4. 测试计划

后端：

- 单测 Test Author Agent：输入零分母 draft，应输出 draft + normal path 对照。
- workflow 端到端测试：最终 test case 数至少为 2，且仍能得到 high risk。
- 继续跑全部 pytest，确保 executor/board 不被新增 passing test 干扰。

前端：

V1.6 不改 UI 主功能，只继续运行：

```text
npm.cmd run build
```

## 不做内容

- 不让 Test Author Agent 直接读写项目文件。
- 不让它绕过 executor sandbox。
- 不自动生成 property-based/fuzz 测试。
- 不引入真实 agent 文件写入能力；这需要更强沙箱设计。

## 完成标准

- workflow 中有独立 `test_author` 节点。
- demo Run 至少执行零分母失败测试和正常路径对照测试。
- 最终风险判断仍保持 high，且证据链更完整。
- Python 测试和前端构建均通过。
