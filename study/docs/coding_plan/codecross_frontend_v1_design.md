# CodeCross 前端 V1 设计稿

V1 优先做三件事：选代码 → 看流程 → 能停。剩下的都先砍。

## 一、V1 范围定线

**做：**
- 新建 Run 页（diff 输入 + 最少配置）
- Run 执行/结果页（同一页面，状态切换）
- 终止按钮（含确认 + 保留已收集证据）
- Pipeline 节点级进度 + 时间线日志
- Findings 列表 + 单条 finding 抽屉

**不做（留给 V2 以后）：**
- 多人协作、评论、标注误报
- 项目仪表盘、跨 run 趋势
- IDE 插件、PR webhook
- 实时 token 流式打字机
- 复杂权限/账户系统

## 二、信息架构

只两条路径：

```
   /                           （Runs 列表，可选；V1 可直接合并到首页）
   ├─ /new                     （新建 Run 表单）
   └─ /runs/:run_id            （Run 详情：进行中 / 已完成 / 已终止）
```

Run 详情页用一个 `status` 字段切换布局：`pending / running / succeeded / failed / aborted`。同一页面，省一次跳转。

## 三、页面 1：新建 Run

```
┌──────────────────────────────────────────────────────────────────────┐
│  CodeCross                                  [Runs]  [设置]  [文档]   │
├──────────────────────────────────────────────────────────────────────┤
│  新建审查                                                            │
│                                                                       │
│  ① 代码变更                                                          │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ [ 粘贴 Diff ] [ 上传 .patch ] [ Git 分支对比 ] [ 加载示例 ]    │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │ diff --git a/examples/sample_project/math_utils.py b/...        │ │
│  │ new file mode 100644                                            │ │
│  │ +def safe_divide(a, b):                                         │ │
│  │ +    return a / b                                               │ │
│  │                                                                  │ │
│  │                                                                  │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ✓ 解析成功 · 1 个文件 · 1 个新增符号 (safe_divide)                  │
│                                                                       │
│  ② 项目根目录                                                        │
│  [ /path/to/project                                       ]  [浏览]  │
│  └ 用作 pytest cwd 与 PYTHONPATH                                     │
│                                                                       │
│  ③ Reviewers                                                          │
│  ☑ logic_reviewer            deepseek-chat       逻辑 / 边界 / 运行时│
│  ☑ security_reviewer         claude-3-5-sonnet   安全 / 维护性       │
│                                                                       │
│  ④ 高级选项  ▾                                                       │
│     最大验证轮次   [ 3 ]                                              │
│     单测超时       [ 20 秒 ]                                          │
│     ☐ 使用 Mock LLM（不调用真实模型，用于演示）                       │
│                                                                       │
│                                          [ 取消 ]   [ ▶ 开始审查 ]   │
└──────────────────────────────────────────────────────────────────────┘
```

设计要点：

- **代码输入用 Tab 切换四种来源**。粘贴 diff 是默认，最低门槛；"加载示例"直接灌入 `examples/diffs/safe_divide.diff`，方便首次用户验证。Git 分支对比可以 V1.1 再做，先把按钮放出来 disabled 即可。
- **diff 输入框下方实时显示解析结果**（文件数、符号数）。失败时高亮报错原因。这是用户提交前的最后一道信心。
- **Reviewer 复选框**直接对应 `ProjectConfig.reviewers`，每行三列：勾选 / 名称 / 模型 / focus。给 V2 留扩展位（自定义 reviewer）。
- **高级选项默认折叠**，避免吓到第一次的用户。展开后字段直接对应 `RunnerConfig`。
- **开始按钮在右下角**，主色按钮；左侧"取消"是次按钮。键盘 `Cmd/Ctrl+Enter` 也可触发。

## 四、页面 2：Run 执行中（V1 重点）

```
┌──────────────────────────────────────────────────────────────────────┐
│  CodeCross  /  Run #abc123                                            │
│                                              [ 复制 ID ]  [ ■ 终止 ] │
├──────────────────────────────────────────────────────────────────────┤
│  ⟳ 执行中  ·  00:42  ·  Round 2 / 3  ·  Tokens 12.3k                 │
│  ████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░  56%                   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Pipeline                                                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  ✓ Planner ──┬── ✓ Reviewer A ──┐                              │ │
│  │              │                    ├─▶ ✓ Board ─▶ ✓ Round 1 ──┐ │ │
│  │              └── ✓ Reviewer B ──┘                              │ │ │
│  │                                                                │ │ │
│  │  ┌─────────────────────────────────────────────────────────────┘ │
│  │  │                                                              │ │
│  │  └─▶ ⟳ Round 2 ──▶ Test Strategy ──▶ Test Case ──▶ Executor    │ │
│  │         (当前)                                                   │ │
│  │                                                                  │ │
│  │  ─▶ Evaluator ─▶ (High Risk Review) ─▶ Final Report             │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  当前步骤                                                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  ⟳ Test Case Agent                                              │ │
│  │  正在为 finding_a1b2 生成 pytest 用例                          │ │
│  │  目标: examples/sample_project/math_utils.py · safe_divide      │ │
│  │  模型: deepseek-chat · 已耗时 4.2s                              │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  执行日志                                       [展开全部] [复制]    │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ 00:00  Planner       ▶ 开始制定审查计划                        │ │
│  │ 00:03  Planner       ✓ 识别 1 文件 / 1 符号 / 2 风险方向       │ │
│  │ 00:03  Reviewer A    ▶ 独立审查中 (deepseek)                   │ │
│  │ 00:03  Reviewer B    ▶ 独立审查中 (claude)                     │ │
│  │ 00:18  Reviewer A    ✓ 完成 · 1 个 high finding                │ │
│  │ 00:21  Reviewer B    ✓ 完成 · 1 个 low finding                 │ │
│  │ 00:21  Board         ✓ 初始化 · 验证队列 1 项                   │ │
│  │ 00:22  Round 1       ▶ 目标: finding_a1b2                       │ │
│  │ 00:25  Test Case     ✓ 生成 1 个用例                           │ │
│  │ 00:28  Executor      ✓ test_safe_divide_zero ... PASSED        │ │
│  │ 00:30  Round 1       ✓ refuted finding_a1b2                     │ │
│  │ 00:30  Round 2       ▶ 目标: finding_a1b2 (复测)                │ │
│  │ 00:42  Test Case     ⟳ 正在生成 ...                            │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  ▾ Reviewer 已发现 (2)                                                │
│  ▾ 已生成测试 (1)                                                     │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

## 五、关键交互规则

### 1. 终止按钮

- **位置**：右上角持续可见，运行状态下用警示色（橙）边框 + 中性底色，避免误触当成主操作。
- **行为**：点击 → 二次确认弹窗 → 后端发送 abort 信号 → 状态变 `aborting` → 收尾后变 `aborted`。
- **二次确认弹窗**：

```
┌──────────────────────────────────────────────────┐
│  终止当前 Run？                                  │
│                                                  │
│  Run #abc123 已运行 00:42（Round 2 / 3）。       │
│  已完成 1 个 reviewer 输出和 1 轮验证。          │
│                                                  │
│  ☑ 保留已收集证据并生成简报                       │
│                                                  │
│  终止后无法恢复，是否继续？                      │
│                                                  │
│                       [ 取消 ]  [ 确认终止 ]     │
└──────────────────────────────────────────────────┘
```

- **"保留已收集证据"勾选项**：后端在 abort 时仍然走一次 evaluator + final_report 但跳过未完成的 round。这是这个产品的差异化——别的工具点终止就什么都没有，CodeCross 能交一份"半成品但可读"的简报。
- **键盘**：`Esc` 不能终止（防误触），`Cmd/Ctrl + .` 触发确认弹窗（IDE 用户熟）。
- **状态转移**：`running → aborting`（按钮文字变"正在终止..."且禁用）→ `aborted`（按钮变"重新开始"）。整个过程禁止退化回 `running`。

### 2. Pipeline 视图

- **节点状态四态**：`pending`（灰）/ `running`（蓝色 + 旋转图标）/ `done`（绿）/ `failed`（红）。
- **静态布局**：节点位置固定写死，按 `workflow.py:399` 的拓扑摆。Reviewer fan-out 是真并行，画成两条平行线，最后汇合到 Board。Round 块是循环结构，画成"折返带计数"。
- **不要让用户拖动**。固定布局调试更容易，截图分享也一致。
- **节点点击**：弹出右侧抽屉，显示该节点的 input / output（直接 dump 对应 state 字段）+ 耗时 + 用到的模型。这层 V1 可以做最简版：只展示 JSON。

### 3. "当前步骤"卡片

放在 Pipeline 下方、日志上方。永远只显示一个，给用户"现在到哪了"的强视觉锚点。卡片内容：

- 节点名 + 友好文案（"Test Case Agent · 正在为 X 生成测试用例"）
- 上下文（目标 finding / 文件 / 符号）
- 当前调用的模型 + 已耗时
- 终止状态下变成"正在终止..."

文案映射建议（一定要做这张表，否则用户看到 `select_verification_targets` 会茫然）：

| 节点名 | 友好文案 |
|---|---|
| `planner` | 制定审查计划 |
| `reviewer_0_logic_reviewer` | 逻辑 Reviewer 独立审查 |
| `reviewer_1_security_maintainability_reviewer` | 安全 Reviewer 独立审查 |
| `initial_board` | 初始化 Review Board |
| `select_verification_targets` | 挑选下轮验证目标 |
| `test_strategy` | 生成测试策略 |
| `test_case` | 生成测试用例 |
| `executor` | 执行 pytest |
| `test_result_analyzer` | 分析测试结果 |
| `coverage_gap` | 汇总覆盖缺口 |
| `targeted_review` | 定向复审 |
| `update_board` | 更新 Board 状态 |
| `evaluator` | 综合风险评估 |
| `high_risk_review` | 高风险二次确认 |
| `final_report` | 生成最终报告 |

### 4. 执行日志

- **每行一条**：`时间 · 节点 · 图标 · 文案`。固定列宽（等宽字体）方便扫读。
- **颜色编码**：图标用色，文案不用。`▶` 蓝（开始）/ `✓` 绿（完成）/ `✗` 红（失败）/ `⟳` 蓝（进行中）。
- **自动滚动**：默认锁底；用户向上滚后取消锁底，右下角出现"回到底部"小按钮。
- **复制按钮**：一键复制纯文本日志，方便粘贴到 issue / IM。

### 5. 进度百分比怎么算

LangGraph 不会告诉你"还剩多少"。简单做法：

- 把流程拆成大阶段并预设权重：Planner 5% · Reviewers 25% · Board 5% · 验证循环 50%（每轮按 `1/max_rounds`）· Evaluator 10% · Report 5%。
- 进度条值是这些大阶段权重之和。
- 不准但稳定，不会回退。
- 如果觉得太假，V1 可以**只显示"Round X / Y"和耗时**，不放百分比。我倾向于这个：诚实优于精确。

## 六、页面 3：Run 已完成（V1 简版）

执行完成时同一页面切换：Pipeline 全部变绿，"当前步骤"卡片替换成"风险评估"卡片，日志保留可折叠。

```
┌──────────────────────────────────────────────────────────────────────┐
│  CodeCross  /  Run #abc123                                            │
│                                  [ 新建 ]  [ 导出 MD ]  [ 导出 JSON ]│
├──────────────────────────────────────────────────────────────────────┤
│  ✓ 完成  ·  耗时 01:24  ·  Tokens 18.7k                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  风险评估                                                             │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  ▲ HIGH                                          置信度: high   │ │
│  │  A high severity finding has supporting test evidence.          │ │
│  │  建议: Block or request changes before merge.                   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  Findings (2)                          [ 全部 ▾ ] [ 按严重度排序 ▾ ] │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ ● HIGH      supported    finding_a1b2                  [ 查看 ]│ │
│  │   Division helper does not handle a zero denominator.           │ │
│  │   logic_reviewer · math_utils.py:1-3                            │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │ ○ LOW       refuted      finding_c3d4                  [ 查看 ]│ │
│  │   No clear security issue found in the bounded diff context.    │ │
│  │   security_reviewer · math_utils.py                             │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  Pipeline (已完成)                              [ 展开历史日志 ▾ ]  │
│  ✓ Planner → ✓ Reviewer A/B → ✓ Round 1 → ✓ Round 2 → ✓ Evaluator   │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

Finding 抽屉（点"查看"打开右侧）：

```
┌────────────────────────────────────────────────────────────┐
│  finding_a1b2                                       [ × ]  │
├────────────────────────────────────────────────────────────┤
│  ▲ HIGH · supported · 来自 logic_reviewer (deepseek-chat)  │
│                                                            │
│  问题                                                      │
│  Division helper does not handle a zero denominator.       │
│                                                            │
│  影响                                                      │
│  Callers can receive ZeroDivisionError ...                 │
│                                                            │
│  证据链                                                    │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ ① 静态判断 · weak                                     │ │
│  │   logic_reviewer 在 diff 中发现 a/b 无零分母处理      │ │
│  │                                                       │ │
│  │ ② pytest 失败 · strong                                │ │
│  │   test_safe_divide_zero_denominator_returns_none      │ │
│  │   ▸ stdout / stderr (点击展开)                        │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  建议                                                      │
│  Define the zero-denominator contract and handle it ...    │
│                                                            │
│  代码位置                                                  │
│  examples/sample_project/math_utils.py:1-3                 │
│                                              [ 在 IDE 打开 ]│
└────────────────────────────────────────────────────────────┘
```

V1 的 Finding 抽屉先做单页静态展示，证据卡片只展示，不能编辑/驳回。这些是 V2 的事。

## 七、视觉规范要点（不细抠，先定基调）

- **色板**：背景近黑 `#0F1115`，主面板 `#161A22`，文字 `#E6E8EB`，次要 `#9099A8`。主色蓝 `#5B8DEF`。
- **风险四档**：critical 红 / high 橙 / medium 黄 / low 绿。整套界面只在徽标和严重度文字处出现这四色，避免乱用。
- **字体**：UI 用系统字体；代码、JSON、日志用 JetBrains Mono / IBM Plex Mono。
- **节奏**：信息密度大，行高紧凑，padding 紧凑。这个产品的用户讨厌空白浪费屏幕。
- **图标**：用 Lucide 一套，避免混搭。

## 八、为支撑这版界面，后端要补的最小 API

V1 不要做 ORM 大数据库。后端用 FastAPI 暴露这几个端点足矣：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/runs` | 创建 Run（body: diff, project_root, reviewers, runner_config）|
| `GET`  | `/api/runs/:id` | 拿 Run 当前状态快照（含 final_report 如果完成）|
| `GET`  | `/api/runs/:id/events` | SSE 流，推 `node_started / node_finished / log / round_started / completed / aborted` 等事件 |
| `POST` | `/api/runs/:id/abort` | 终止；body 带 `keep_partial: true/false` |
| `GET`  | `/api/runs/:id/findings/:fid` | 单条 finding 详情（含 evidence 链）|
| `GET`  | `/api/runs/:id/export` | `?format=markdown` 或 `?format=json` |

存储起步 SQLite 单表（`run_id, status, created_at, request, snapshot_json`）就够。

## 九、推荐的实施顺序

如果按"每天能看到东西"的节奏做，建议：

1. 后端：把 `run_workflow` 改成 `stream_workflow`，输出适配后的事件 → SSE。
2. 后端：`/api/runs` POST + `/api/runs/:id/abort`（abort 用一个 `threading.Event` 在节点入口处检查即可，最粗暴但能用）。
3. 前端：新建 Run 页（只支持粘贴 diff）+ Run 详情页（执行中布局）。Pipeline 用 React Flow 或者直接 SVG 都行。
4. 前端：终止按钮 + 二次确认 + 状态切换。
5. 前端：完成态布局 + Findings 列表 + 抽屉。
6. 收尾：示例 diff 一键加载、导出 MD/JSON、节点点击看 input/output 抽屉。

整体节奏估计是一个有经验的全栈工程师专注做能出 V1。

## 十、几个 V1 容易过度设计的地方，提醒一下

- **不要做账号/团队/权限**。本地单用户，run 用 UUID 即可。
- **不要做实时 token 流式打字机**。SSE 已经够用，token 流是后期锦上添花。
- **不要做 Pipeline 自由布局编辑**。固定。
- **不要做"项目"概念**。每次都是一次独立的 Run，列表页按时间倒序就行。多项目/多仓库是 V3 的事。
- **不要做 IDE 跳转的真实集成**。"在 IDE 打开"按钮 V1 复制路径到剪贴板就够了。
