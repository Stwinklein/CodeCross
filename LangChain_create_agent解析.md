# CodeCross V1 设计文档

> 当前目标：先把 CodeCross 的产品边界、核心架构、状态 schema、Agent Loop 和版本范围确定清楚，再进入代码实现。

---

## 1. 项目定位

**CodeCross 是一个多模型交叉代码审查与证据验证系统。**

它的核心目标不是让多个 Agent 互相聊天，而是：

```text
让多个 AI Reviewer 独立审查同一份 commit / PR diff，
分别生成 review finding 和测试意图，
先汇入 Review Board 形成初始证据黑板，
再由 Evidence Verification Loop 围绕未验证结论进行测试验证和定向复审，
最后形成 Final Board，
由 Evaluator 基于证据进行风险判断和结果仲裁。
```

V1 的关键词是：

```text
Multi Reviewers
Independent Review
Evidence Verification Loop
Finding Verification
Agentic Testing
Review Board
Evidence-Based Evaluation
High Risk Review
Configurable Models
```

---

## 2. V1 核心结论

V1 不退回单 Reviewer。

V1 的核心链路不是“Test Runner 包住所有 Reviewer”，而是：

```text
Independent Multi Reviewers
  → Initial Review Board
  → Evidence Verification Loop
  → Final Review Board
  → Evaluator
```

也就是说：

```text
Reviewer 负责提出风险假设。
Board 负责管理证据和验证状态。
Verification Loop 负责验证、补测、必要时定向复审。
Evaluator 负责基于 Final Board 做最终风险仲裁。
```

V1 也不把 Test Runner 设计成一个只能执行固定测试用例的普通程序。

更合理的拆法是：

```text
Evidence Verification Loop
  = Board-driven loop
  + Agentic Test Runner
  + Optional Targeted Review

Agentic Test Runner
  = Testing Agents
  + Deterministic Executor
```

也就是说：

```text
Agent 负责：
- 理解 diff 和 findings
- 设计测试策略
- 生成测试用例
- 根据失败结果继续补测
- 判断覆盖缺口

确定性执行器负责：
- 创建临时测试文件
- 执行 pytest / 函数调用 / 静态命令
- 控制超时
- 收集 stdout / stderr / exit_code
- 返回结构化结果
```

这样既保留 Agent 的探索能力，也避免让 LLM 直接“声称测试通过”。

每个 finding 在 Board 中都需要有验证状态：

```text
unverified    尚未验证
supported     已有测试或证据支持该问题存在
refuted       已有测试或证据表明该问题大概率不存在
inconclusive  尝试验证但证据不足
not_testable  不适合自动测试验证，只能人工或规则判断
```

Loop 的核心目标不是无限增加测试，而是让高风险 finding 尽快从：

```text
unverified / inconclusive
```

收敛到：

```text
supported / refuted / not_testable
```

---

## 3. V1 总体架构

```text
Commit / PR Diff
      ↓
Planner
      ↓
Multi Reviewers 并行独立审查
  ┌───────────────┬───────────────┐
  ↓               ↓               ↓
Reviewer A     Reviewer B     Reviewer C
  │               │               │
  └──── findings + test intents ──┘
                  ↓
          Initial Review Board
                  ↓
       Evidence Verification Loop
        ┌─────────┬─────────┐
        ↓         ↓         ↓
   Agentic     Targeted   Board
   Testing     Review     Update
                  ↓
            Final Review Board
                  ↓
              Evaluator
                  ↓
       High Risk Review / Final Report
```

更具体地说：

```text
Planner
  ↓
fan-out 到多个 Reviewer
  ↓
Reviewer 独立生成 findings + test_intents
  ↓
Initial Board 记录 finding、test intent 和验证状态
  ↓
Evidence Verification Loop 读取未验证或证据不足的问题
  ↓
Test Strategy Agent 生成测试策略
  ↓
Test Case Agent 生成可执行测试
  ↓
Deterministic Executor 执行测试
  ↓
Test Result Analyzer 分析失败/错误/覆盖缺口
  ↓
必要时触发 Targeted Reviewer 做定向复审
  ↓
Board 更新 finding verification 状态
  ↓
循环直到高风险问题收敛或达到预算
  ↓
Final Board 汇总 findings、test cases、test results、coverage gaps、verification status
  ↓
Evaluator 判断风险等级
  ↓
如果 high risk，进入 High Risk Review
  ↓
输出最终报告
```

---

## 4. 核心设计原则

### 4.1 Reviewer 第一轮必须独立

V1 不让 Reviewer 之间直接交流，也不让后面的 Reviewer 读取前面 Reviewer 的结论。

原因是防止锚定效应。

错误设计：

```text
Reviewer A → Reviewer B → Reviewer C
```

正确设计：

```text
             ┌→ Reviewer A
Planner ─────┼→ Reviewer B
             └→ Reviewer C
```

所有 Reviewer 独立读取：

```text
diff + review_plan + 必要上下文
```

然后分别输出：

```text
findings + test_intents
```

### 4.2 Reviewer 不直接决定最终结论

Reviewer 只负责提出问题、证据、怀疑点和测试意图。

最终结论交给：

```text
Review Board + Evidence Verification Loop + Evaluator
```

系统不应该因为某个模型说得像就直接相信它，而应该看：

```text
有没有证据
测试是否能复现
失败是否稳定
问题是否具体
影响范围是否明确
多个 Reviewer 是否独立命中同类问题
```

### 4.3 Test Runner 是验证子系统，不是总控制器

V1 的 Test Runner 不应该只是固定程序，因为固定程序只能执行已有测试，不能主动发现覆盖缺口。

但 Test Runner 也不能完全变成 LLM 口头判断。

因此 V1 把 Test Runner 放在 Evidence Verification Loop 内部：

```text
Evidence Verification Loop
  ├── Board selects unresolved evidence targets
  ├── Agentic Test Runner generates and executes tests
  ├── Optional Targeted Reviewer re-checks ambiguous evidence
  └── Board updates verification status
```

Test Runner 内部采用：

```text
Testing Agents 负责探索和生成测试
Executor 负责真实执行和返回证据
```

这点是 CodeCross 和普通 AI Review 项目的关键区别。

关键边界：

```text
Multi Reviewers 不属于 Test Runner。
Test Runner 不直接决定最终风险等级。
Test Runner 只产出测试证据和覆盖结论。
```

### 4.4 Review Board 是证据黑板

Board 不是单纯总结文本，而是整个系统的共享证据层。

它应该保存：

```text
每个 Reviewer 的 finding
每个 finding 的来源
每个测试用例的来源
每轮测试的执行结果
失败日志
重复/冲突/未覆盖问题
Evaluator 使用过的证据
```

### 4.5 Evaluator 应优先规则化

Evaluator 可以使用 LLM 辅助总结，但风险等级在 V1 应该先用规则驱动。

原因是风险判断需要稳定、可复现、可解释。

示例：

```text
critical/security finding → high
failed_tests >= 1 且关联 high finding → high
同类 medium finding 被多个 Reviewer 独立命中 → medium/high
只有 low findings 且测试通过 → low
测试执行失败但原因不明 → medium
```

### 4.6 Loop 退出由 Board 状态驱动

Loop 不应该因为 Agent “还想继续测”就无限继续。

V1 的 loop 由 Board 中每个 finding 的验证状态驱动。

```text
unverified    → 需要进入验证队列
supported     → 不再反复审查，只进入最终报告
refuted       → 不再反复测试，只保留反证记录
inconclusive  → 如果是高风险，继续补测或定向复审
not_testable  → 不再自动测试，交给 Evaluator/人工判断
```

因此 loop 的核心问题不是“还能不能生成更多测试”，而是：

```text
高风险 finding 是否已经有足够证据进入 supported / refuted / not_testable？
```

---

## 5. V1 模块职责

## 5.1 Planner

### 输入

```text
单次 commit diff / PR diff
有限代码上下文
项目语言和测试框架配置
```

### 职责

```text
分析本次变更范围
识别受影响文件、函数、类、接口
生成 review_plan
生成 testing_focus
告诉 Reviewer 应该重点看什么
告诉 Test Runner 应该优先验证什么
```

### V1 不做

```text
不做复杂 repo 级上下文检索
不做历史 bug 知识库
不做自动任务拆分到大量文件
不做跨服务调用链分析
```

### 输出

```text
ReviewPlan
```

示例：

```text
本次变更新增 divide(a, b)，重点检查：
1. 除零风险
2. 参数类型边界
3. 返回值正确性
4. 调用方异常处理

测试重点：
1. b = 0
2. 正负数混合
3. 非数字输入
4. 整数与浮点返回值
```

---

## 5.2 Multi Reviewers

V1 至少保留两个 Reviewer，因为这是 CodeCross 的核心差异。

推荐 V1 配置：

```text
Reviewer A：DeepSeek Reviewer
Reviewer B：Second Channel Reviewer
```

后续可以扩展成：

```text
DeepSeek Reviewer
GPT Reviewer
Claude Reviewer
Qwen Reviewer
Local Rule Reviewer
Security Reviewer
```

### 每个 Reviewer 的输入

```text
diff
review_plan
testing_focus
有限上下文
项目配置
```

### 每个 Reviewer 的职责

```text
独立审查代码
发现潜在问题
生成结构化 finding
生成 test_intents
说明为什么这些测试有价值
```

### 每个 Reviewer 的输出

```text
review_findings
test_intents
reviewer_notes
```

### finding 结构

```text
finding_id
source_reviewer
model
category
severity
confidence
file_path
line_range
problem
evidence
impact
suggestion
reproduction_hint
related_test_intent_ids
```

### test intent 结构

```text
test_intent_id
source_reviewer
target_file
target_symbol
scenario
reason
expected_behavior
risk_category
priority
```

说明：

```text
Reviewer 输出的是 test_intents，不强制直接输出可执行测试代码。
可执行测试由 Test Runner 的测试生成 Agent 统一生成。
```

---

## 5.3 Agentic Test Runner

Agentic Test Runner 是 Evidence Verification Loop 内部的测试验证子系统。

它不是总控制器，也不包含初始 Multi Reviewers。

它负责把 Board 上的未验证 finding / test_intents 转换成可执行测试，并把真实执行结果回写给 Board。

### 输入

```text
diff
review_plan
testing_focus
review_findings
test_intents
有限代码上下文
项目测试配置
```

### 输出

```text
test_session
test_cases
test_results
coverage_summary
unresolved_test_gaps
verification_evidence
```

### 内部组成

```text
Test Strategy Agent
Test Case Agent
Test Sanitizer
Deterministic Executor
Test Result Analyzer
Coverage Gap Agent
```

### 5.3.1 Test Strategy Agent

职责：

```text
根据 diff、findings、test_intents 制定测试策略
决定优先测试哪些风险点
把自然语言测试意图转换成测试任务
标记高优先级测试场景
```

输出：

```text
test_strategy
test_tasks
coverage_targets
```

### 5.3.2 Test Case Agent

职责：

```text
把 test_tasks 转换成可执行测试
可以生成 pytest 测试代码
可以生成函数调用型测试用例
可以生成边界值测试
可以生成简单 property-based 测试建议
```

V1 优先支持：

```text
Python
pytest
函数级测试
临时测试文件
```

### 5.3.3 Test Sanitizer

职责：

```text
校验测试代码是否结构完整
去重测试用例
过滤危险命令
限制文件写入范围
限制网络访问
检查 import 是否合理
检查测试是否真的断言了行为
```

V1 需要至少做到：

```text
重复 test case 去重
无断言测试过滤
明显危险代码过滤
超长测试过滤
```

### 5.3.4 Deterministic Executor

职责：

```text
创建临时测试文件
执行 pytest 或函数调用
设置 timeout
收集 stdout / stderr / exit_code
返回结构化 TestResult
```

注意：

```text
Executor 不是 Agent。
Executor 不做主观判断。
Executor 只负责真实执行。
```

### 5.3.5 Test Result Analyzer

职责：

```text
分析测试失败原因
区分代码 bug、测试生成错误、环境错误、依赖错误
把失败结果关联回 finding / test_intent
判断失败是否稳定
提出下一轮补测方向
```

### 5.3.6 Coverage Gap Agent

职责：

```text
判断当前测试是否覆盖了主要风险点
发现还没测试到的分支、边界、异常路径
决定是否进入下一轮测试
```

输出：

```text
suggest_continue_verification: true / false
new_test_tasks
stop_reason
coverage_gaps
```

### 5.3.7 Optional Targeted Reviewer

Targeted Reviewer 不是第一轮 Reviewer。

它只在 Evidence Verification Loop 中被条件触发，用于处理测试证据不足、测试失败原因不清、或 Reviewer 之间结论冲突的情况。

触发条件示例：

```text
某个 high finding 连续测试后仍 inconclusive
测试失败但无法判断是代码问题还是测试生成问题
两个 Reviewer 对同一位置给出相反判断
Coverage Gap Agent 判断关键路径无法通过测试覆盖
```

职责：

```text
只读取相关 finding、测试结果、失败日志和必要上下文
不重新做完整代码审查
判断现有证据是否支持该 finding
提出更精确的补测方向或标记 not_testable
```

输出：

```text
targeted_review_notes
verification_suggestion
new_test_intents
suggested_status_update
```

---

## 5.4 Evidence Verification Loop

Evidence Verification Loop 是 V1 的主循环。

它不是单纯的测试循环，而是围绕 Board 上的 finding 验证状态推进。

基本 loop：

```text
round 1:
  从 Board 读取 unverified high / medium findings
  根据 Reviewer test_intents 生成初始测试
  执行测试
  分析结果
  更新 finding verification status

round 2:
  只针对 inconclusive / 高风险未覆盖问题补充测试
  执行测试
  分析结果
  必要时触发 Targeted Reviewer

round 3:
  针对仍不确定的高风险点做最后补测或定向复审
  执行测试
  输出 unresolved gaps 和最终 verification status
```

### Finding 验证状态

每个 finding 在 Board 中都必须有一个验证状态：

```text
unverified:
  尚未被测试或证据验证。

supported:
  已有测试失败、静态证据、日志或复现路径支持该问题存在。

refuted:
  相关测试通过，或证据表明原 finding 大概率不成立。

inconclusive:
  已尝试验证，但测试结果不足、环境失败、或证据冲突。

not_testable:
  问题不适合自动测试验证，例如架构可维护性、命名、复杂设计取舍。
```

### 状态转移规则

```text
unverified → supported:
  测试失败稳定复现，且失败原因与 finding 对齐。

unverified → refuted:
  目标行为测试通过，且没有发现相关失败证据。

unverified → inconclusive:
  测试生成失败、环境失败、结果不稳定，或证据不足。

inconclusive → supported:
  后续补测或定向复审找到明确支持证据。

inconclusive → refuted:
  后续补测证明原风险不成立。

inconclusive → not_testable:
  自动测试无法覆盖该问题，继续测试收益很低。
```

### 停止条件

```text
满足任一条件即可退出：

1. 所有 high / critical findings 已进入 supported / refuted / not_testable
2. 所有 high priority test_intents 已执行或被标记为不可执行
3. 当前轮没有产生新的有效测试
4. 当前轮没有产生新的证据
5. 连续两轮 finding verification status 没有变化
6. 达到 max_verification_rounds
7. 达到 timeout_budget
8. 达到 token_budget
9. 达到 max_generated_tests
10. 测试环境持续失败，无法继续判断
```

说明：

```text
不是所有 low finding 都必须测完。
V1 的优先目标是让高风险问题有结论。
已经 supported 的 finding 不再反复审查。
已经 refuted 的 finding 不再反复测试。
只有 inconclusive、高风险未覆盖、证据冲突的问题进入下一轮。
```

### V1 默认配置

```text
max_verification_rounds: 3
max_tests_per_round: 8
max_generated_tests: 24
test_timeout_seconds: 30
allow_network: false
allow_file_write_outside_temp: false
fail_on_dangerous_test_code: true
```

### Loop 的价值

单轮测试只能验证第一批显而易见的场景。

Evidence Verification Loop 可以做到：

```text
根据失败日志继续缩小问题
根据未覆盖分支继续补测
根据 Reviewer 分歧生成对照测试
根据异常行为生成回归测试
根据证据状态决定是否需要定向复审
把最终结论绑定到具体证据，而不是模型投票
```

---

## 5.5 Review Board

Board 是系统的共享看板和证据聚合层。

### 输入

```text
review_findings
test_intents
test_cases
test_results
test_rounds
coverage_summary
unresolved_test_gaps
finding_verifications
evidence_items
targeted_review_notes
```

### 职责

```text
汇总所有 Reviewer 的发现
汇总所有测试意图
汇总所有测试执行结果
标记失败测试
标记重复测试
标记冲突意见
把测试结果关联到 finding
维护每个 finding 的验证状态
挑选下一轮需要验证的问题
生成 board_summary
```

### V1 Board 必须做

```text
统计 Reviewer 数量
统计 finding 数量
统计 test_intent 数量
统计 test_case 数量
统计 test_round 数量
统计 passed / failed / error / skipped
列出失败详情
列出未覆盖风险点
列出 finding verification 状态
列出 supported / refuted / inconclusive / not_testable findings
保留每条证据来源
```

### V1 Board 的验证队列

Board 需要维护下一轮验证队列：

```text
verification_queue
```

入队优先级：

```text
1. critical / high 且 unverified
2. critical / high 且 inconclusive
3. 多个 Reviewer 独立命中的 medium
4. 有失败测试但未关联 finding 的异常行为
5. 覆盖缺口对应的高优先级 test_intent
```

不再入队：

```text
supported findings
refuted findings
not_testable findings
纯风格 low findings
```

### V1 Board 至少要做的轻量去重

为了支持 Evaluator 判断“多个 Reviewer 是否重复命中”，V1 需要一个简单规则：

```text
same file_path
+ overlapping line_range
+ same category
≈ same issue cluster
```

这不需要复杂语义聚类，但要能形成：

```text
finding_clusters
```

### V2 Board 再做

```text
语义级 finding 合并
冲突 finding 标记
多数投票
置信度加权
模型历史表现权重
```

---

## 5.6 Evaluator

Evaluator 是风险判断模块。

它不应该重新做完整代码审查，而是基于 Board 的信息做判断。

### 输入

```text
diff
review_plan
review_findings
finding_clusters
finding_verifications
test_results
coverage_summary
unresolved_test_gaps
board_summary
```

### 职责

```text
判断整体风险等级
筛选高价值 finding
判断是否进入 High Risk Review
生成 final_report 草稿
说明风险判断依据
区分 verified risk 与 unverified suspicion
```

### 风险等级

```text
critical
high
medium
low
unknown
```

### V1 风险判断规则

```text
critical:
- 明确安全风险并有可复现证据
- 明确数据丢失风险并有失败测试
- 会导致核心路径不可用

high:
- 测试失败且关联 high severity finding，并且 verification_status = supported
- 多个 Reviewer 独立命中同一严重问题，且至少有部分证据支持
- 高优先级测试意图被验证失败
- 关键异常路径没有处理且可复现

medium:
- 有潜在 bug，但证据不足
- 有失败测试但更像环境问题或测试生成问题
- 有未覆盖高风险路径或 high finding 仍为 inconclusive
- 多个 low/medium finding 指向同一模块质量问题

low:
- 测试通过
- high / critical finding 已被 refuted 或 not_testable 且无其他证据
- finding 主要是风格、命名、轻微可维护性问题
- 无明确高风险问题

unknown:
- Reviewer 大量失败
- Test Runner 无法执行
- 关键 finding 长时间停留在 unverified
- 上下文不足以判断
```

### Evaluator 输出

```text
risk_level
confidence
top_findings
evidence_summary
failed_tests_summary
coverage_gaps
verification_summary
recommended_action
needs_high_risk_review
final_report
```

---

## 5.7 High Risk Review

High Risk Review 只在 Evaluator 判断为 high / critical 时触发。

### 输入

```text
diff
high risk findings
failed test results
coverage_gaps
verification_status
board_summary
evaluator report
```

### 职责

```text
二次确认高风险问题
解释 bug 成因
判断测试失败是否真正对应代码问题
判断 inconclusive 高风险问题是否需要人工关注
给出修复建议
给出最小回归测试建议
```

### V1 不做

```text
不自动改代码
不自动提交 PR
不做多轮修复
不接入远程 A2A 服务
```

---

## 6. LangGraph 编排设计

V1 使用 LangGraph 做外层状态机。

## 6.1 流程图

```text
START
  ↓
planner
  ↓
fan-out
  ├── deepseek_reviewer
  ├── second_reviewer
  └── optional_reviewer_n
  ↓
fan-in
  ↓
initial_board
  ↓
select_verification_targets
  ↓
test_strategy_agent
  ↓
test_case_agent
  ↓
test_sanitizer
  ↓
deterministic_executor
  ↓
test_result_analyzer
  ↓
coverage_gap_agent
  ↓
optional_targeted_reviewer
  ↓
update_board_verification_status
  ├── continue → select_verification_targets
  └── stop
  ↓
final_board
  ↓
evaluator
  ├── high / critical → high_risk_review → END
  └── low / medium / unknown → END
```

---

## 6.2 State 设计

V1 的核心状态应该包含：

```text
diff
repo_context
project_config

plan
testing_focus

reviews
findings
test_intents

test_strategy
test_rounds
test_cases
test_results
coverage_summary
unresolved_test_gaps
verification_queue
finding_verifications
evidence_items
targeted_reviews

finding_clusters
board_summary

risk_level
evaluator_report
high_risk_report
final_report

errors
warnings
```

多个 Reviewer 会同时写：

```text
reviews
findings
test_intents
```

Agentic Test Runner 会追加写：

```text
test_rounds
test_cases
test_results
coverage_summary
unresolved_test_gaps
```

Evidence Verification Loop 会更新：

```text
verification_queue
finding_verifications
evidence_items
targeted_reviews
board_summary
```

所以这些字段需要支持追加合并：

```text
reviews: list append
findings: list append
test_intents: list append
test_rounds: list append
test_cases: list append
test_results: list append
targeted_reviews: list append
evidence_items: list append
finding_verifications: keyed merge by finding_id
```

---

## 7. 核心 Schema

V1 必须先固定 schema，否则后面所有 Agent 输出都会不稳定。

## 7.1 ProjectConfig

```text
language
test_framework
package_manager
source_roots
test_roots
entry_points
allowed_commands
blocked_commands
model_config
runner_config
```

## 7.2 ReviewPlan

```text
plan_id
changed_files
changed_symbols
risk_areas
review_focus
testing_focus
context_notes
```

## 7.3 Finding

```text
finding_id
source_reviewer
model
category
severity
confidence
file_path
line_range
problem
evidence
impact
suggestion
reproduction_hint
related_test_intent_ids
```

Finding 本身只描述 Reviewer 的审查结论。

验证状态不要直接由 Reviewer 决定，而是由 Board 根据测试证据和定向复审结果维护。

## 7.4 FindingVerification

```text
finding_id
status
evidence_ids
related_test_result_ids
related_targeted_review_ids
last_updated_round
reason
confidence
next_action
```

status 可选：

```text
unverified
supported
refuted
inconclusive
not_testable
```

## 7.5 EvidenceItem

```text
evidence_id
evidence_type
source
related_finding_ids
related_test_case_id
related_test_result_id
related_targeted_review_id
summary
raw_reference
strength
created_round
```

evidence_type 可选：

```text
test_failure
test_pass
executor_error
static_evidence
targeted_review_note
coverage_gap
manual_note
```

strength 可选：

```text
strong
medium
weak
```

## 7.6 TestIntent

```text
test_intent_id
source_reviewer
target_file
target_symbol
scenario
reason
expected_behavior
risk_category
priority
related_finding_ids
```

## 7.7 TestCase

```text
test_case_id
source_agent
source_test_intent_ids
related_finding_ids
round_index
test_type
target_file
target_symbol
test_name
test_code
expected_behavior
assertions
timeout_seconds
requires_external_service
safety_level
```

test_type 可选：

```text
pytest
function_call
property_based
static_check
manual
```

## 7.8 TestResult

```text
test_result_id
test_case_id
round_index
status
exit_code
stdout
stderr
duration_ms
failure_summary
error_type
is_reproducible
related_finding_ids
```

status 可选：

```text
passed
failed
error
skipped
timeout
duplicate
unsafe
```

## 7.9 TestRound

```text
round_index
input_focus
generated_test_case_ids
executed_test_case_ids
new_failures
new_coverage_notes
verification_status_changes
continue_verification
stop_reason
```

## 7.10 TargetedReview

```text
targeted_review_id
round_index
target_finding_ids
input_test_result_ids
reviewer_name
model
analysis
suggested_status_updates
new_test_intents
not_testable_reasons
```

## 7.11 BoardSummary

```text
reviewer_count
finding_count
test_intent_count
test_case_count
test_round_count
passed_count
failed_count
error_count
skipped_count
finding_clusters
finding_verifications
verification_queue
failed_tests
coverage_gaps
evidence_map
```

## 7.12 EvaluatorReport

```text
risk_level
confidence
top_findings
evidence_summary
failed_tests_summary
coverage_gaps
verification_summary
recommended_action
needs_high_risk_review
reasoning
```

---

## 8. 多 Reviewer 状态流转

V1 不做 Reviewer 之间直接状态流转。

正确方式是：

```text
Reviewer A → 写入 findings / test_intents
Reviewer B → 写入 findings / test_intents
Reviewer C → 写入 findings / test_intents
Initial Board → 接收所有 findings / test_intents 并初始化验证状态
Evidence Verification Loop → 围绕 verification_queue 生成测试、执行测试、定向复审
Final Board → 汇总完整证据和 verification status
Evaluator → 读取 Final Board 结果
```

也就是说，Reviewer 之间不是：

```text
A 传给 B
B 传给 C
```

而是：

```text
A/B/C 都独立写入共享证据层，
Board 决定哪些问题需要继续验证。
```

这个架构本质上是：

```text
Blackboard Architecture
```

---

## 9. 模型渠道设计

V1 必须支持模型可配置。

不要把模型写死在代码里。

建议抽象为：

```text
ModelAdapter
```

但 V1 不需要做复杂插件系统，只需要统一配置和统一调用接口。

### V1 默认模型角色

```text
planner:
  model: configurable
  role: 变更理解、审查计划、测试重点

deepseek_reviewer:
  model: deepseek-chat
  role: 逻辑 bug、边界条件、异常分支

second_reviewer:
  model: configurable
  role: 可维护性、安全风险、接口设计、测试覆盖遗漏

test_strategy_agent:
  model: configurable
  role: 测试策略和覆盖目标

test_case_agent:
  model: configurable
  role: 生成可执行测试

test_result_analyzer:
  model: configurable
  role: 分析失败原因和补测方向

coverage_gap_agent:
  model: configurable
  role: 判断覆盖缺口和 loop 停止

targeted_reviewer:
  model: configurable
  role: 针对 inconclusive / evidence conflict 做定向复审，不做完整重审

evaluator:
  model: configurable
  role: 报告总结，风险等级优先由规则决定

high_risk_reviewer:
  model: configurable
  role: 高风险二次确认
```

### 示例配置

```text
models:
  default_timeout_seconds: 60
  default_max_tokens: 4000

  planner:
    provider: deepseek
    model: deepseek-chat

  reviewers:
    - name: logic_reviewer
      provider: deepseek
      model: deepseek-chat
      focus: logic, boundary, exception
      enabled: true

    - name: security_maintainability_reviewer
      provider: openai
      model: configurable
      focus: security, maintainability, api-design
      enabled: true

  test_runner_agents:
    strategy:
      provider: configurable
      model: configurable

    case_generator:
      provider: configurable
      model: configurable

    result_analyzer:
      provider: configurable
      model: configurable

    coverage_gap:
      provider: configurable
      model: configurable

  targeted_reviewer:
    provider: configurable
    model: configurable

  evaluator:
    provider: configurable
    model: configurable
```

### 配置项

```text
reviewer_count
enabled_reviewers
parallelism
timeout_seconds
max_tokens
temperature
retry_count
fail_open
```

建议：

```text
Reviewer temperature 可以略高，以增加发现角度
Evaluator temperature 应该低，以保证稳定
Test Case Agent temperature 应该低到中等，以保证测试可执行
```

---

## 10. 上下文策略

V1 不做完整 repo 级检索，但必须定义有限上下文。

Reviewer 和 Test Runner 可读取：

```text
diff
changed file snippets
changed function / class
直接调用方或被调用方签名
项目测试框架配置
已有相关测试文件片段
```

V1 不默认读取：

```text
整个仓库
历史 bug 库
所有依赖源码
其他 Reviewer 输出
```

注意：

```text
Reviewer 不读取其他 Reviewer 输出。
Evidence Verification Loop 可以读取所有 Reviewer 的 findings 和 test_intents。
Agentic Test Runner 只读取 Board 分配给本轮的 verification targets 和必要上下文。
Targeted Reviewer 只读取相关 finding、测试结果、失败日志和必要上下文。
Evaluator 可以读取 Board 的完整证据。
```

---

## 11. 失败路径设计

V1 必须明确失败路径，否则系统一遇到模型或测试失败就无法收敛。

### Reviewer 失败

```text
单个 Reviewer 超时 → 记录 warning，其他 Reviewer 继续
超过一半 Reviewer 失败 → risk_level 至少 unknown
Reviewer JSON 解析失败 → 尝试一次 repair
repair 失败 → 丢弃该 Reviewer 输出并记录错误
```

### Evidence Verification / Test Runner 失败

```text
测试生成失败 → 进入下一轮或记录 unresolved gap
测试代码 unsafe → 标记 unsafe，不执行
pytest 环境缺失 → 标记 environment_error
测试执行 timeout → 标记 timeout
Targeted Reviewer 失败 → 保留原 finding 状态，不阻塞其他问题
连续两轮执行环境失败 → 停止 loop，相关 finding 标记 inconclusive，risk_level 倾向 unknown/medium
```

### Evaluator 失败

```text
Evaluator Agent 失败 → 使用规则 Evaluator 输出
规则 Evaluator 也缺证据 → risk_level = unknown
```

---

## 12. 安全与隐私

CodeCross 会把代码 diff 发送给多个模型，因此必须提前设计安全边界。

V1 需要配置：

```text
allow_remote_models
allowed_providers
redact_secrets
store_prompts
store_model_responses
max_context_chars
```

Test Runner 安全限制：

```text
禁止网络访问，除非显式开启
禁止写入项目目录外文件，除非显式开启
禁止执行 destructive 命令
所有测试在临时目录中执行
所有命令必须有 timeout
```

后续如果接入企业项目，应支持：

```text
local-only mode
model allowlist
prompt/response audit log
secret scanning
```

---

## 13. A2A 的位置

A2A 不放在 V1 核心链路里。

它是后期扩展点。

V1 需要预留的是：

```text
统一 Reviewer 输入输出 schema
统一 Test Agent 输入输出 schema
统一 ModelAdapter 接口
```

未来某个 Reviewer 可以从：

```text
本地 create_agent reviewer
```

替换成：

```text
Dify reviewer via A2A
Spring AI reviewer via A2A
远程安全审查服务 via A2A
```

但外层流程不变：

```text
Planner → Reviewers → Initial Board → Evidence Verification Loop → Final Board → Evaluator
```

---

## 14. 历史 Bug 知识库的位置

历史 Bug 知识库不放 V1。

它应该作为后期上下文增强模块。

推荐接入位置：

```text
Planner 前后
Reviewer 调用前
Test Strategy Agent 调用前
```

也就是：

```text
diff
  ↓
检索相关 bug pattern
  ↓
Planner / Reviewer / Test Strategy Agent
```

不要让 Agent 全量读取历史记忆。

应该按下面维度检索：

```text
project_id
file_path
bug_category
language
framework
changed_symbol
```

---

## 15. V1 不做的东西

为了保证可落地，V1 明确不做：

```text
不做自动修复
不做自动提交 PR
不接 A2A
不接 Dify / Spring AI
不做完整历史知识库
不做复杂 repo 级上下文检索
不做真实 Docker 沙箱
不做完整覆盖率统计
不做跨语言测试生成
不做多轮代码修改回归
```

V1 要做：

```text
Multi Reviewers
Initial Review Board
Evidence Verification Loop
Agentic Test Runner
Deterministic Test Execution
Final Review Board
Rule-first Evaluator
High Risk Review
Configurable Models
```

---

## 16. V1-MVP 落地范围

为了避免一开始做得过大，V1-MVP 固定为：

```text
输入：
- 一个 diff 文本
- Python 项目配置
- pytest 可用

Reviewer：
- 至少 2 个 Reviewer
- 并行独立审查
- 输出 Finding + TestIntent

Test Runner：
- 支持 pytest 测试生成
- 作为 Evidence Verification Loop 内的测试验证子系统
- 每轮最多 8 个测试
- 支持 timeout
- 支持 unsafe 测试过滤

Evidence Verification Loop：
- 支持最多 3 轮验证
- 以 finding verification status 驱动
- 只继续处理 high / critical / inconclusive / evidence conflict
- 已 supported / refuted / not_testable 的 finding 不再反复进入 loop

Board：
- 汇总 findings / test intents / test cases / results / verification status
- 轻量 finding cluster
- 维护 verification_queue
- 输出 board_summary

Evaluator：
- 规则优先判断 risk_level
- LLM 只辅助生成报告文本

High Risk：
- high / critical 时触发二次分析
- 不自动修代码
```

---

## 17. V1 代码结构设计

V1 第一版不要拆太细。

目标是：

```text
先让学习和调试成本最低，
让整个 CodeCross 主流程能一眼看懂，
后面稳定后再按模块拆分。
```

推荐第一版控制在 7-8 个核心文件：

```text
codecross/
  __init__.py
  schemas.py
  config.py
  llm.py
  agents.py
  executor.py
  board.py
  workflow.py
  main.py
```

### 17.1 schemas.py

负责所有核心数据结构。

第一版所有 schema 都放在一个文件里，不要过早拆成多个小文件。

包含：

```text
ReviewPlan
Finding
FindingVerification
EvidenceItem
TestIntent
TestCase
TestResult
TestRound
TargetedReview
BoardSummary
EvaluatorReport
```

原则：

```text
所有 Agent 输入输出都尽量走 schema
所有 Board 状态也尽量走 schema
不要让自然语言 dict 在系统里到处流动
```

---

### 17.2 config.py

负责模型配置和运行配置。

包含：

```text
模型 provider / model name
Reviewer 列表
每个 Reviewer 的 focus
max_verification_rounds
max_tests_per_round
max_generated_tests
test_timeout_seconds
是否允许网络
是否允许写项目目录外文件
fail_open / fail_closed
```

第一版可以先用 Python dict 或简单 dataclass。

后续稳定后再考虑：

```text
YAML 配置
TOML 配置
环境变量覆盖
项目级配置文件
```

---

### 17.3 llm.py

负责统一模型调用入口。

包含：

```text
ModelConfig
call_model()
call_model_json()
简单 retry
简单 JSON repair
```

第一版不要做复杂 provider 插件系统。

只需要保留统一接口：

```text
response = call_model(
    role="reviewer",
    model_config=config,
    messages=messages,
)
```

后续再扩展：

```text
OpenAI
DeepSeek
Claude
Qwen
local model
A2A remote agent
```

---

### 17.4 agents.py

负责所有 Agent 节点逻辑。

第一版不要拆成：

```text
planner.py
reviewer.py
test_strategy.py
test_case.py
coverage.py
targeted_review.py
evaluator.py
```

这些后面可以拆，但 V1 先集中在一个文件里，方便理解整条链路。

包含：

```text
planner_agent()
reviewer_agent()
test_strategy_agent()
test_case_agent()
test_result_analyzer_agent()
coverage_gap_agent()
targeted_reviewer_agent()
evaluator_agent()
high_risk_review_agent()
```

职责：

```text
组装 prompt
调用 llm.py
解析结构化输出
返回 schema 对象或 workflow state patch
```

注意：

```text
agents.py 不直接执行 pytest
agents.py 不直接修改 Board 内部状态
agents.py 只负责“思考和生成结构化输出”
```

---

### 17.5 executor.py

负责确定性测试执行。

这个文件不要调用 LLM。

包含：

```text
写临时测试文件
运行 pytest
timeout 控制
收集 stdout / stderr / exit_code
清理临时文件
返回 TestResult
```

职责边界：

```text
Executor 不判断风险等级
Executor 不决定是否继续 loop
Executor 不修改 finding verification status
Executor 只返回真实执行证据
```

---

### 17.6 board.py

负责 Review Board 的证据管理和状态更新。

包含：

```text
init_board()
init_finding_verifications()
build_verification_queue()
cluster_findings()
add_evidence()
update_finding_verification()
build_board_summary()
select_next_verification_targets()
should_continue_verification()
```

职责：

```text
初始化每个 finding 的验证状态
维护 verification_queue
维护 evidence_map
把 TestResult / TargetedReview 转成 EvidenceItem
更新 supported / refuted / inconclusive / not_testable
判断下一轮该验证什么
生成 BoardSummary
```

注意：

```text
Board 不调用 LLM
Board 不执行测试
Board 是规则化的证据状态管理层
```

---

### 17.7 workflow.py

负责 LangGraph 编排。

这里只写流程，不塞具体业务逻辑。

包含：

```text
build_graph()
run_workflow()
planner_node()
reviewer_nodes()
initial_board_node()
select_verification_targets_node()
test_strategy_node()
test_case_node()
executor_node()
test_result_analyzer_node()
coverage_gap_node()
targeted_review_node()
update_board_node()
evaluator_node()
high_risk_review_node()
```

核心原则：

```text
workflow.py 负责“谁先谁后”
agents.py 负责“Agent 怎么想”
executor.py 负责“测试怎么跑”
board.py 负责“证据怎么记、状态怎么变”
```

---

### 17.8 main.py

负责 CLI / demo 入口。

第一版只需要支持最简单的运行方式：

```text
读取 diff 文本
加载默认 config
运行 workflow
打印 final_report
打印 board_summary
```

示例：

```text
python -m codecross.main --diff examples/simple.diff
```

后续再扩展：

```text
读取 git commit
读取 GitHub PR diff
输出 JSON report
输出 Markdown report
接入 Web UI
```

---

### 17.9 第一版不要过度拆分

暂时不要这样拆：

```text
planner.py
reviewer.py
test_strategy.py
test_case.py
coverage.py
targeted_review.py
evaluator.py
finding_schema.py
test_schema.py
board_schema.py
model_openai.py
model_deepseek.py
```

这些拆法后面都可以做，但 V1 不建议一开始就做。

原因：

```text
文件太多会增加学习成本
Agent 之间的输入输出还没稳定
Board 状态规则还可能调整
过早拆分会让重构成本变高
```

V1 的最小心智模型应该是：

```text
schemas.py   数据长什么样
config.py    系统怎么配置
llm.py       模型怎么调用
agents.py    Agent 负责想
executor.py  测试负责跑
board.py     Board 负责记证据和决定下一步
workflow.py  LangGraph 负责串起来
main.py      Demo / CLI 入口
```

---

### 17.10 后续拆分时机

当满足下面条件后，再考虑拆文件：

```text
agents.py 超过 800-1000 行
某个 Agent prompt 和解析逻辑明显独立
Board 状态规则稳定
schema 很少变动
需要支持多个语言或多个测试框架
需要接入多个模型 provider
```

可能的后续结构：

```text
codecross/
  agents/
    planner.py
    reviewers.py
    testing.py
    evaluator.py
  models/
    base.py
    openai_adapter.py
    deepseek_adapter.py
  testing/
    pytest_executor.py
    sanitizer.py
  board/
    board.py
    clustering.py
    verification.py
```

但这不是 V1-MVP 的目标。

---

## 18. V1 成功标准

V1 跑通后应该能做到：

```text
输入一个简单 diff

Planner 生成 review_plan 和 testing_focus

两个 Reviewer 独立输出：
- findings
- test_intents

Initial Board：
- 初始化 finding verification status
- 生成 verification_queue

Evidence Verification Loop：
- 挑选需要验证的 finding
- 生成可执行 pytest
- 真实执行测试
- 根据失败、覆盖缺口或证据冲突继续补测
- 必要时触发 Targeted Reviewer
- 更新 supported / refuted / inconclusive / not_testable
- 最多 3 轮后收敛

Final Board 汇总：
- reviewer 数量
- finding 数量
- test intent 数量
- test case 数量
- test round 数量
- failed test 数量
- unresolved gaps
- verification status 分布

Evaluator 输出：
- risk_level
- evidence_summary
- final_report

如果 high / critical：
- High Risk Review 生成二次分析
```

可验证指标：

```text
所有 Agent 输出可以被 schema 校验
任意 Reviewer 失败不会导致全流程崩溃
测试执行结果必须来自真实 executor
loop 退出必须有明确 stop_reason
同一个 diff 多次运行 risk_level 基本稳定
Board 能追踪每条结论的证据来源
```

---

## 19. 后续版本规划

### V1：Agentic Multi Review 基础版

```text
Planner
Multi Reviewers
Initial Review Board
Evidence Verification Loop
Agentic Test Runner
Deterministic Executor
Final Review Board
Rule-first Evaluator
High Risk Review
```

重点是跑通：

```text
fan-out / fan-in
finding verification loop
evidence board
risk evaluation
```

### V1.1：Board 增强

加入：

```text
更好的 finding 合并
冲突 finding 标记
Reviewer 来源追踪增强
失败测试到 finding 的更精确关联
```

### V1.2：Test Runner 增强

加入：

```text
覆盖率工具接入
property-based testing
flaky test 复跑
自动生成 regression test 草案
测试生成质量评分
```

### V2：真实项目测试环境

加入：

```text
临时 workspace
依赖安装策略
subprocess 执行隔离
更严格超时控制
错误日志收集
多测试框架支持
```

### V3：多轮修复建议

加入：

```text
Review → Test → Fix Suggestion → Regression Test
max_iteration = 3
每轮只处理 top priority
```

注意：

```text
V3 仍然可以不自动改代码，
只做修复建议和回归验证。
```

### V4：历史 Bug 知识库

加入：

```text
历史 bug pattern 检索
项目 review rules
文件级上下文增强
模型表现记录
```

### V5：A2A 外部 Reviewer

加入：

```text
Dify reviewer
Spring AI reviewer
远程 reviewer service
远程 test agent service
```

A2A 作为 Reviewer Node 或 Test Agent Node 的远程实现方式，不改变主流程。

---

## 20. 项目亮点表达

这个项目可以这样表达：

> CodeCross 是一个基于 LangGraph 的多模型交叉代码审查与证据验证系统。系统采用 fan-out/fan-in 多 Reviewer 架构，让不同模型独立审查同一份 commit diff，并生成结构化 finding 和测试意图。所有初始审查结果先进入 Review Board，Board 为每个 finding 维护 unverified、supported、refuted、inconclusive、not_testable 等验证状态。随后 Evidence Verification Loop 围绕未验证或证据不足的高风险问题，调用 Agentic Test Runner 生成和执行测试，并在必要时触发 Targeted Reviewer 做定向复审。最终 Final Board 汇总审查意见、测试证据、覆盖缺口和验证状态，由规则优先的 Evaluator 基于证据进行风险评估，并对高风险问题触发二次审查。系统预留统一 ModelAdapter、Reviewer schema 和 Test Agent schema，支持后续接入 A2A、Dify、Spring AI 或本地模型服务。

---

## 21. 最终一句话架构

```text
CodeCross V1 =
LangGraph 状态机
+ 多模型 Reviewer 并行独立审查
+ Board 驱动的 Evidence Verification Loop
+ Agentic Test Runner 测试验证
+ 确定性测试执行证据
+ 结构化 Review Board 验证状态
+ 规则优先 Evaluator 风险仲裁
```

当前设计方向固定为：

> **V1 做 Multi Reviewers + Board-driven Evidence Verification Loop；Reviewer 独立提出风险，Board 管理证据状态，Agentic Test Runner 验证结论，Targeted Reviewer 只处理证据不足的问题，Evaluator 基于 Final Board 做风险仲裁。**
