# CodeCross V1.1 版本修复计划

本文基于 `pasted-text.txt` 中列出的 20 个问题做取舍。V1.1 只修最关键的核心问题：安全底线、误判风险、真实模型可运行性、低成本稳定性。不做大规模架构重写。

## 版本目标

V1.1 的目标不是扩展能力，而是让现有 V1 主链路更可信、更安全、更容易排错：

- 不让模型生成的测试轻易伤到宿主环境。
- 不让一次测试结果过度决定 finding 结论。
- 不让真实模型调用因为参数或异常处理问题静默失败。
- 不为了“完善架构”改动过大。

## 必须修复

### P0. Executor 安全检查过薄

来源问题：#2 Executor 沙箱过于薄

当前 `executor.py` 主要依赖字符串黑名单拦截危险测试代码，容易误杀，也容易被绕过。V1.1 不建议直接上 Docker / nsjail 这类重方案，先做最小安全加固：

- 用 `ast.parse` + `ast.walk` 检查危险 import / 调用。
- 禁止 `os.system`、`subprocess.*`、`eval`、`exec`、`compile`、`open(..., "w")`、`socket`、`requests` 等高风险能力。
- 保留现有 `RunnerConfig.blocked_commands` 作为第二层兜底，而不是主防线。
- 继续使用临时测试文件执行，但明确限制 cwd 为 `project_root`。

验收标准：

- 对 `__import__("os").system("...")` 这类绕过写法能拦截。
- 对普通 pytest 断言不误杀。
- 被拦截时返回 `unsafe` 状态，并写入清晰原因。

### P0. 真实模型错误处理过宽

来源问题：#14 错误处理过宽

当前部分 agent 捕获 `Exception` 后统一吞成 reviewer/test-case failed，容易掩盖代码 bug。V1.1 需要收紧异常边界：

- 只捕获模型调用、网络、超时、Pydantic 校验这类可预期异常。
- 其它异常向上抛出，避免把代码 bug 包装成模型失败。
- 错误结果里保留异常类型和简短 message。

验收标准：

- 网络/超时错误不会中断整个流程，但报告中可见。
- schema 字段写错、代码类型错误不会被静默吞掉。

### P1. Anthropic 调用参数对齐

来源问题：#19 Anthropic 调用参数不对齐

这是小改动但影响真实模型链路。V1.1 需要确认当前 LangChain Anthropic provider 接受的参数名，并把 provider 参数差异集中处理。

- 检查 `init_chat_model` 对 Anthropic 当前版本实际参数。
- 将 `max_tokens_to_sample` 改为当前 provider 接受的字段，例如 `max_tokens`。
- 建一个 provider 参数映射函数，避免散落 if/else。

验收标准：

- `--demo --project-root .` 在真实 Anthropic 配置下不会因为参数名报错。
- DeepSeek 路径不受影响。

### P1. Diff 解析最小加固

来源问题：#15 Diff 解析不健壮

当前正则解析够 demo，但对 rename、binary diff、hunk 行号不稳。V1.1 不做完整代码索引，只替换 diff parser：

- 引入 `unidiff` 或等价轻量解析库。
- 输出 changed files、hunk 新旧行号、是否 binary/rename。
- 将 hunk 行号传给 reviewer prompt 和 finding 初始上下文。

验收标准：

- 普通 diff、rename diff、多个 hunk 都能解析。
- reviewer 至少能拿到 changed file 和 hunk line range，不完全靠模型猜行号。

### P1. Verification Loop 防误判和早停

来源问题：#1 证据可信度建模偏弱，#7 Verification Loop 终止条件过粗

完整证据可信度体系可以放后面，但 V1.1 至少要避免最危险的误判和重复验证：

- `passed -> refuted` 不再直接给 high confidence，默认降为 medium。
- `failed -> supported` 前增加一次重跑，重跑一致才提升 confidence。
- `error/timeout` 不直接下结论，先重跑一次；仍失败再标 inconclusive。
- 如果一轮没有任何 `verification_status_changes`，提前停止验证。

验收标准：

- flaky 测试不会一次失败就把 finding 直接 supported high。
- 连续无进展不会跑满所有 max rounds。
- 报告里能看到 early stop 的 reason。

## 明确延后

以下问题重要，但不进入 V1.1，避免改动过大：

- #3 Diff 上下文检索：价值很高，但需要符号索引 / grep / LSP 设计，放 V1.2。
- #4 Reviewer 独立性重构：会改 planner/reviewer 契约，放 V1.2。
- #5 Test Case 生成能力增强：fixture、mock、Hypothesis 属于能力扩展，放 V1.2+。
- #6 多语言 executor：需要 Strategy 抽象，放 V2。
- #8 RiskRules 配置化：先保持规则简单，放 V1.2。
- #9 语义聚类和 reviewer 分歧：需要 embedding 或更复杂聚类，放 V1.2+。
- #10 配置外部化：有价值，但不是当前稳定性瓶颈，放 V1.2。
- #11 观测性、#12 CI/SARIF、#18 HTML/SARIF 报告：生产化增强，放 V2。
- #13 缓存：成本优化，放 V2。
- #16 State 拆分：架构重构，放 V2。
- #17 Prompt 工程系统化：可逐步做，但不作为 V1.1 阻塞项。
- #20 测试体系扩展：随上述修复补关键单测，不单独做大测试工程。

## 建议实施顺序

1. 修 Anthropic 参数和异常处理，先保证真实模型链路可诊断。
2. 加 Executor AST 安全检查，并补 sanitizer 单测。
3. 替换 diff parser，并补 rename / multi-hunk 单测。
4. 修改 verification 更新规则，加入重跑和无进展早停。
5. 跑 `--mock-llm` 端到端和现有 pytest，确认 V1 demo 行为不退化。

## V1.1 不做的边界

- 不引入 Docker / 容器沙箱。
- 不重写 LangGraph 拓扑。
- 不做多语言支持。
- 不做项目/账号/CI 平台集成。
- 不重构整个 state schema。

这个范围足够小，但能解决 V1 当前最核心的风险：安全、误判、真实运行失败和重复验证浪费。
