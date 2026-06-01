# `frontend/src/App.tsx` 解析

## 文件作用
CodeCross 前端主应用，实现新建审查、历史记录、运行详情、实时日志、pipeline 可视化、finding 抽屉和报告导出。

- 源码行数：2452
- 主要顶层常量/静态数据：`SAMPLE_DIFF`（第 217 行）、`reviewers`（第 227 行）、`pipelineNodes`（第 244 行，已包含 `report_localizer` 节点）、`nodeOrder`（第 383 行）、`executionEvents`（第 385 行）、`sampleFindings`（第 507 行）、`sourceTabs`（第 554 行）、`statusLabels`（第 561 行）、`apiEventTypes`（第 570 行）。

## 类型定义
- `ViewName`（类型，第 30 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `RunStatus`（类型，第 31 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `NodeStatus`（类型，第 32 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `LogKind`（类型，第 33 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `Severity`（类型，第 34 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `VerificationStatus`（类型，第 35 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `IconComponent`（类型，第 36 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ReviewerOption`（类型，第 38 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ParseResult`（类型，第 46 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `PipelineNode`（类型，第 54 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `LogRow`（类型，第 64 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `EvidenceItem`（类型，第 72 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `Finding`（类型，第 79 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `DrawerState`（类型，第 93 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRunStatus`（类型，第 97 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRunEventType`（类型，第 98 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRunEvent`（类型，第 107 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiEvaluatorReport`（类型，第 117 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiFinding`（类型，第 125 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiFindingVerification`（类型，第 138 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiEvidenceItem`（类型，第 144 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiBoardSummary`（类型，第 152 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiSnapshot`（类型，第 159 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRunRecord`（类型，第 166 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRunSummary`（类型，第 180 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRuntimeReviewer`（类型，第 194 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ApiRuntimeConfig`（类型，第 203 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。
- `ReviewerOverrideDraft`（类型，第 208 行）：定义结构化对象/类型，帮助模块之间保持清晰的数据边界。

## 函数与组件


### `appendLog`（第 490 行）
- 作用：向时间线追加日志并去重，避免 SSE 与轮询同时到达时重复显示。
- 源码形态：`function appendLog(previous: LogRow[], next: LogRow)`

### `App`（第 570 行）
- 作用：前端根组件，集中管理路由状态、diff 输入、run 状态、SSE/轮询、历史记录、抽屉和弹窗。
- 源码形态：`function App()`

### `Header`（第 1121 行）
- 作用：顶部导航组件，提供新建审查、历史记录和当前运行入口。
- 源码形态：`function Header({ onNew, onRuns }: { onNew: () => void; onRuns: () => void })`

### `NewRunView`（第 1146 行）
- 作用：新建审查页面，负责 diff 输入、项目根目录、reviewer 开关、高级配置和启动 run。
- 源码形态：`function NewRunView(props:`

### `HistoryView`（第 1358 行）
- 作用：历史记录页面，展示后端保存的 run 摘要并支持打开旧 run。
- 源码形态：`function HistoryView(props:`

### `ReviewerConnectionEditor`（第 1424 行）
- 作用：单次 run 的 reviewer 连接配置编辑器，可覆盖模型、Base URL、API key 或 A2A 地址。
- 源码形态：`function ReviewerConnectionEditor(props:`

### `RunView`（第 1503 行）
- 作用：运行详情页面，组合状态、pipeline、finding、日志、导出和取消操作。
- 源码形态：`function RunView(props:`

### `ParseStatus`（第 1651 行）
- 作用：展示浏览器端 diff 解析结果，例如文件数、符号数、增量行和错误提示。
- 源码形态：`function ParseStatus({ result }: { result: ParseResult })`

### `CurrentStepCard`（第 1660 行）
- 作用：当前步骤摘要卡，显示执行节点、进度和状态。
- 源码形态：`function CurrentStepCard(`

### `FailureSummary`（第 1689 行）
- 作用：失败摘要组件，把 API/workflow 错误以可读方式展示。
- 源码形态：`function FailureSummary({ error }: { error: string })`

### `RiskSummary`（第 1708 行）
- 作用：风险摘要组件，从后端快照中提取最终风险、证据和建议。
- 源码形态：`function RiskSummary({ report, status }: { report?: ApiEvaluatorReport; status: RunStatus })`

### `PipelineView`（第 1737 行）
- 作用：审查 pipeline 可视化组件，展示节点位置、连接线和状态。
- 源码形态：`function PipelineView(`

### `NodeIcon`（第 1811 行）
- 作用：根据节点状态选择 pipeline 图标。
- 源码形态：`function NodeIcon({ status }: { status: NodeStatus })`

### `FindingsPanel`（第 1818 行）
- 作用：Finding 列表面板，支持按严重度过滤和打开详情。
- 源码形态：`function FindingsPanel(`

### `LogPanel`（第 1878 行）
- 作用：执行日志面板，显示本地模拟日志或后端事件流日志。
- 源码形态：`function LogPanel(`

### `InspectorDrawer`（第 1920 行）
- 作用：详情抽屉分发组件，根据 drawer 类型展示节点或 finding。
- 源码形态：`function InspectorDrawer({ drawer, onClose }: { drawer: DrawerState | null; onClose: () => void })`

### `NodeDrawer`（第 1938 行）
- 作用：节点详情抽屉，解释某个 workflow 节点的模型、职责和状态。
- 源码形态：`function NodeDrawer({ drawer }: { drawer: Extract<DrawerState, { type: "node" }> })`

### `FindingDrawer`（第 1968 行）
- 作用：Finding 详情抽屉，展示问题、影响、建议和关联证据。
- 源码形态：`function FindingDrawer({ finding }: { finding: Finding })`

### `AbortDialog`（第 2007 行）
- 作用：取消运行确认弹窗，处理是否保留部分结果等选择。
- 源码形态：`function AbortDialog(`

### `mapApiStatus`（第 2057 行）
- 作用：把后端 run 状态映射为前端展示状态，补齐 `aborting` 这类 UI 中间态。
- 源码形态：`function mapApiStatus(status: ApiRunStatus): RunStatus`

### `reviewerModelLabel`（第 2063 行）
- 作用：把 reviewer 的 provider/model 组合成页面可读标签。
- 源码形态：`function reviewerModelLabel(reviewer: ApiRuntimeReviewer)`

### `reviewersFromRuntimeConfig`（第 2067 行）
- 作用：把后端运行时配置转换成前端 reviewer 选项。
- 源码形态：`function reviewersFromRuntimeConfig(config: ApiRuntimeConfig): ReviewerOption[]`

### `splitModelLabel`（第 2079 行）
- 作用：拆分模型标签，供配置编辑器预填 provider 和 model。
- 源码形态：`function splitModelLabel(label: string)`

### `defaultReviewerOverride`（第 2084 行）
- 作用：为某个 reviewer 生成默认的前端覆盖配置草稿。
- 源码形态：`function defaultReviewerOverride(reviewer: ReviewerOption): ReviewerOverrideDraft`

### `buildReviewerOverridesPayload`（第 2095 行）
- 作用：把前端草稿转换成后端 `POST /api/runs` 接受的 reviewer override payload。
- 源码形态：`function buildReviewerOverridesPayload(`

### `modelForPipelineNode`（第 2119 行）
- 作用：根据 pipeline 节点 ID 找到对应模型标签；`report_localizer` 会读取后端 runtime config 里的 `report_localizer` 模型角色。
- 源码形态：`function modelForPipelineNode(node: PipelineNode, config: ApiRuntimeConfig | null)`

### `applyRuntimeConfigToPipeline`（第 2139 行）
- 作用：把真实后端模型标签写回 pipeline 节点展示数据。
- 源码形态：`function applyRuntimeConfigToPipeline(nodes: PipelineNode[], config: ApiRuntimeConfig | null)`

### `apiProgressPercent`（第 2143 行）
- 作用：根据后端状态和当前节点计算进度百分比。
- 源码形态：`function apiProgressPercent(status: RunStatus, activeNodeIndex: number)`

### `normalizeSeverity`（第 2154 行）
- 作用：把 API 返回的 severity 规范化为前端枚举。
- 源码形态：`function normalizeSeverity(value: string | undefined): Severity`

### `normalizeVerificationStatus`（第 2159 行）
- 作用：把 API 返回的 verification status 规范化为前端枚举。
- 源码形态：`function normalizeVerificationStatus(value: string | undefined): VerificationStatus`

### `normalizeEvidenceStrength`（第 2165 行）
- 作用：把 evidence strength 规范化为弱/中/强。
- 源码形态：`function normalizeEvidenceStrength(value: string | undefined): EvidenceItem["strength"]`

### `formatApiLineRange`（第 2170 行）
- 作用：把后端行号范围格式化为 UI 文本。
- 源码形态：`function formatApiLineRange(lineRange: ApiFinding["line_range"])`

### `evidenceForFinding`（第 2178 行）
- 作用：从 Board evidence_map 和 finding verification 中收集某个 finding 的证据列表。
- 源码形态：`function evidenceForFinding(snapshot: ApiSnapshot, findingId: string, finding: ApiFinding): EvidenceItem[]`

### `findingsFromSnapshot`（第 2201 行）
- 作用：把后端 workflow snapshot 转换为前端 Finding 列表。
- 源码形态：`function findingsFromSnapshot(snapshot: ApiSnapshot | null | undefined): Finding[]`

### `nodeIdFromApiEvent`（第 2225 行）
- 作用：把后端事件映射到 pipeline 节点 ID。
- 源码形态：`function nodeIdFromApiEvent(event: ApiRunEvent)`

### `logKindFromApiEvent`（第 2232 行）
- 作用：把后端事件类型映射到前端日志类型。
- 源码形态：`function logKindFromApiEvent(event: ApiRunEvent): LogKind`

### `logFromApiEvent`（第 2240 行）
- 作用：把一个 API 事件转换成可显示的日志行。
- 源码形态：`function logFromApiEvent(event: ApiRunEvent, index: number): LogRow`

### `appendApiEventLog`（第 2250 行）
- 作用：把 API 事件日志追加到当前日志列表，同时复用去重逻辑。
- 源码形态：`function appendApiEventLog(previous: LogRow[], event: ApiRunEvent)`

### `logsFromApiRun`（第 2255 行）
- 作用：从完整 run 记录恢复日志列表，用于刷新或打开历史 run。
- 源码形态：`function logsFromApiRun(record: ApiRunRecord): LogRow[]`

### `parseDiff`（第 2338 行）
- 作用：在浏览器端轻量解析 unified diff，用于启动前即时反馈。
- 源码形态：`function parseDiff(diffText: string): ParseResult`

### `logIcon`（第 2375 行）
- 作用：根据日志类型选择展示图标。
- 源码形态：`function logIcon(kind: LogKind)`

### `formatElapsed`（第 2383 行）
- 作用：把秒数格式化为运行时长。
- 源码形态：`function formatElapsed(seconds: number)`

### `formatDateTime`（第 2393 行）
- 作用：把 ISO 时间格式化为本地日期时间。
- 源码形态：`function formatDateTime(value: string)`

### `exportRun`（第 2399 行）
- 作用：导出当前前端状态中的报告内容。
- 源码形态：`function exportRun(format: "json" | "markdown", runId: string, logs: LogRow[])`

### `downloadBlob`（第 2430 行）
- 作用：创建临时下载链接，把 Blob 保存为本地文件。
- 源码形态：`function downloadBlob(blob: Blob, fileName: string)`

## 学习提示
建议先读顶部类型和静态 pipeline 数据，再读 `App` 的状态管理，最后读各个展示组件和 API 转换 helper。
