import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, RefObject } from "react";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clipboard,
  Copy,
  Download,
  Eye,
  FileJson,
  FileText,
  FileUp,
  FolderOpen,
  GitBranch,
  Loader2,
  Play,
  RotateCcw,
  Settings,
  ShieldCheck,
  Square,
  Terminal,
  X,
} from "lucide-react";

type ViewName = "new" | "run" | "history";
type RunStatus = "pending" | "running" | "aborting" | "succeeded" | "failed" | "aborted";
type NodeStatus = "pending" | "running" | "done" | "failed";
type LogKind = "start" | "running" | "done" | "failed" | "abort";
type Severity = "critical" | "high" | "medium" | "low";
type VerificationStatus = "supported" | "refuted" | "inconclusive" | "unverified";
type IconComponent = ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;

type ReviewerOption = {
  id: string;
  name: string;
  provider: string;
  model: string;
  focus: string;
};

type ParseResult = {
  ok: boolean;
  message: string;
  files: string[];
  symbols: string[];
  additions: number;
};

type PipelineNode = {
  id: string;
  label: string;
  friendly: string;
  detail: string;
  model: string;
  x: number;
  y: number;
};

type LogRow = {
  second: number;
  nodeId: string;
  actor: string;
  kind: LogKind;
  message: string;
};

type EvidenceItem = {
  title: string;
  strength: "weak" | "medium" | "strong";
  summary: string;
  raw?: string;
};

type Finding = {
  id: string;
  severity: Severity;
  status: VerificationStatus;
  source: string;
  model: string;
  problem: string;
  impact: string;
  suggestion: string;
  file: string;
  line: string;
  evidence: EvidenceItem[];
};

type DrawerState =
  | { type: "node"; node: PipelineNode; status: NodeStatus }
  | { type: "finding"; finding: Finding };

type ApiRunStatus = "pending" | "running" | "succeeded" | "failed" | "aborted";
type ApiRunEventType =
  | "created"
  | "running"
  | "node"
  | "succeeded"
  | "failed"
  | "abort_requested"
  | "aborted";

type ApiRunEvent = {
  event_id: number;
  run_id: string;
  type: ApiRunEventType;
  message: string;
  created_at: string;
  node?: string | null;
  payload?: Record<string, unknown>;
};

type ApiEvaluatorReport = {
  risk_level?: string;
  confidence?: string;
  evidence_summary?: string;
  recommended_action?: string;
  reasoning?: string;
};

type ApiFinding = {
  finding_id?: string;
  source_reviewer?: string;
  model?: string;
  severity?: string;
  file_path?: string;
  line_range?: { start?: number | null; end?: number | null };
  problem?: string;
  evidence?: string;
  impact?: string;
  suggestion?: string;
};

type ApiFindingVerification = {
  status?: string;
  evidence_ids?: string[];
  reason?: string;
};

type ApiEvidenceItem = {
  evidence_type?: string;
  source?: string;
  summary?: string;
  raw_reference?: string;
  strength?: string;
};

type ApiBoardSummary = {
  finding_count?: number;
  failed_count?: number;
  finding_verifications?: Record<string, ApiFindingVerification>;
  evidence_map?: Record<string, ApiEvidenceItem>;
};

type ApiSnapshot = {
  findings?: ApiFinding[];
  board_summary?: ApiBoardSummary;
  evaluator_report?: ApiEvaluatorReport;
  final_report?: string;
};

type ApiRunRecord = {
  run_id: string;
  status: ApiRunStatus;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  final_report?: string;
  snapshot?: ApiSnapshot | null;
  error?: string;
  cancel_requested?: boolean;
  events?: ApiRunEvent[];
};

type ApiRunSummary = {
  run_id: string;
  status: ApiRunStatus;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
  project_root: string;
  mock_llm: boolean;
  risk_level: string;
  finding_count: number;
  error: string;
  cancel_requested: boolean;
};

type ApiRuntimeReviewer = {
  id: string;
  name: string;
  provider: string;
  model: string;
  focus: string;
  enabled: boolean;
};

type ApiRuntimeConfig = {
  reviewers: ApiRuntimeReviewer[];
  role_models: Record<string, string>;
};

type ReviewerOverrideDraft = {
  provider: string;
  model: string;
  baseUrl: string;
  apiKey: string;
  useA2a: boolean;
  a2aUrl: string;
};

const SAMPLE_DIFF = `diff --git a/examples/sample_project/math_utils.py b/examples/sample_project/math_utils.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/examples/sample_project/math_utils.py
@@
+def safe_divide(a, b):
+    return a / b
`;

const reviewers: ReviewerOption[] = [
  {
    id: "logic_reviewer",
    name: "logic_reviewer",
    provider: "deepseek",
    model: "后端配置读取中",
    focus: "逻辑 / 边界 / 运行时",
  },
  {
    id: "security_maintainability_reviewer",
    name: "security_maintainability_reviewer",
    provider: "anthropic",
    model: "后端配置读取中",
    focus: "安全 / API / 维护性",
  },
];

const pipelineNodes: PipelineNode[] = [
  {
    id: "planner",
    label: "Planner",
    friendly: "制定审查计划",
    detail: "识别变更文件、符号和风险方向。",
    model: "后端配置读取中",
    x: 9,
    y: 21,
  },
  {
    id: "reviewer_0_logic_reviewer",
    label: "Reviewer A",
    friendly: "逻辑 Reviewer 独立审查",
    detail: "关注逻辑、边界条件、运行时异常和可维护性。",
    model: "后端配置读取中",
    x: 32,
    y: 12,
  },
  {
    id: "reviewer_1_security_maintainability_reviewer",
    label: "Reviewer B",
    friendly: "安全 Reviewer 独立审查",
    detail: "关注安全、API 设计、失败路径和测试遗漏。",
    model: "后端配置读取中",
    x: 32,
    y: 31,
  },
  {
    id: "initial_board",
    label: "Board",
    friendly: "初始化 Review Board",
    detail: "收集 reviewer 输出，初始化 finding、验证状态和静态证据。",
    model: "local rules",
    x: 56,
    y: 21,
  },
  {
    id: "select_verification_targets",
    label: "Round 1",
    friendly: "挑选下轮验证目标",
    detail: "从 Board 验证队列中选择当前轮需要证明或驳回的 finding。",
    model: "local rules",
    x: 76,
    y: 21,
  },
  {
    id: "test_strategy",
    label: "Test Strategy",
    friendly: "生成测试策略",
    detail: "把目标 finding 转成测试覆盖目标和停止条件。",
    model: "后端配置读取中",
    x: 10,
    y: 61,
  },
  {
    id: "test_case",
    label: "Test Case",
    friendly: "生成测试用例",
    detail: "为目标 finding 生成可执行 pytest 用例。",
    model: "后端配置读取中",
    x: 26,
    y: 61,
  },
  {
    id: "test_author",
    label: "Test Author",
    friendly: "审校测试用例",
    detail: "补强测试草稿，加入正常路径对照并保持 sandbox 安全边界。",
    model: "后端配置读取中",
    x: 42,
    y: 61,
  },
  {
    id: "executor",
    label: "Executor",
    friendly: "执行 pytest",
    detail: "在项目根目录内运行生成的测试，并采集 stdout/stderr。",
    model: "pytest",
    x: 58,
    y: 61,
  },
  {
    id: "test_result_analyzer",
    label: "Analyzer",
    friendly: "分析测试结果",
    detail: "将 pytest 结果转换为 Board 可用的验证建议。",
    model: "后端配置读取中",
    x: 74,
    y: 61,
  },
  {
    id: "update_board",
    label: "Update Board",
    friendly: "更新 Board 状态",
    detail: "写回证据、状态变化和下一轮验证策略。",
    model: "local rules",
    x: 90,
    y: 61,
  },
  {
    id: "evaluator",
    label: "Evaluator",
    friendly: "综合风险评估",
    detail: "按证据和严重度给出最终风险等级。",
    model: "后端配置读取中",
    x: 43,
    y: 86,
  },
  {
    id: "high_risk_review",
    label: "High Risk",
    friendly: "高风险二次确认",
    detail: "当风险等级较高时，由第二模型做一次确认。",
    model: "后端配置读取中",
    x: 64,
    y: 86,
  },
  {
    id: "final_report",
    label: "Final Report",
    friendly: "生成最终报告",
    detail: "输出 Markdown 报告和结构化快照。",
    model: "local renderer",
    x: 85,
    y: 86,
  },
];

const nodeOrder = pipelineNodes.map((node) => node.id);

const executionEvents: LogRow[] = [
  {
    second: 0,
    nodeId: "planner",
    actor: "Planner",
    kind: "start",
    message: "开始制定审查计划",
  },
  {
    second: 3,
    nodeId: "planner",
    actor: "Planner",
    kind: "done",
    message: "识别 1 文件 / 1 符号 / 2 风险方向",
  },
  {
    second: 4,
    nodeId: "reviewer_0_logic_reviewer",
    actor: "Reviewer A",
    kind: "start",
    message: "逻辑 Reviewer 独立审查中",
  },
  {
    second: 4,
    nodeId: "reviewer_1_security_maintainability_reviewer",
    actor: "Reviewer B",
    kind: "start",
    message: "安全 Reviewer 独立审查中",
  },
  {
    second: 18,
    nodeId: "reviewer_0_logic_reviewer",
    actor: "Reviewer A",
    kind: "done",
    message: "完成 · 1 个 high finding",
  },
  {
    second: 21,
    nodeId: "reviewer_1_security_maintainability_reviewer",
    actor: "Reviewer B",
    kind: "done",
    message: "完成 · 1 个 low finding",
  },
  {
    second: 22,
    nodeId: "initial_board",
    actor: "Board",
    kind: "done",
    message: "初始化 · 验证队列 1 项",
  },
  {
    second: 23,
    nodeId: "select_verification_targets",
    actor: "Round 1",
    kind: "start",
    message: "目标: finding_a1b2",
  },
  {
    second: 25,
    nodeId: "test_strategy",
    actor: "Strategy",
    kind: "done",
    message: "生成测试策略 · 覆盖零分母路径",
  },
  {
    second: 28,
    nodeId: "test_case",
    actor: "Test Case",
    kind: "done",
    message: "生成 1 个 pytest 用例",
  },
  {
    second: 31,
    nodeId: "executor",
    actor: "Executor",
    kind: "done",
    message: "test_safe_divide_zero_denominator ... FAILED",
  },
  {
    second: 35,
    nodeId: "test_result_analyzer",
    actor: "Analyzer",
    kind: "done",
    message: "证据强度 strong · finding_a1b2 supported",
  },
  {
    second: 38,
    nodeId: "update_board",
    actor: "Board",
    kind: "done",
    message: "更新验证状态 · supported",
  },
  {
    second: 43,
    nodeId: "evaluator",
    actor: "Evaluator",
    kind: "done",
    message: "风险等级 high · 建议阻塞合并",
  },
  {
    second: 48,
    nodeId: "high_risk_review",
    actor: "High Risk",
    kind: "done",
    message: "二次确认完成 · 保持 high",
  },
  {
    second: 52,
    nodeId: "final_report",
    actor: "Report",
    kind: "done",
    message: "生成 CodeCross V1 Report",
  },
];

function appendLog(previous: LogRow[], next: LogRow) {
  const exists = previous.some(
    (log) => log.second === next.second && log.nodeId === next.nodeId && log.message === next.message,
  );
  return exists ? previous : [...previous, next];
}

const sampleFindings: Finding[] = [
  {
    id: "finding_a1b2",
    severity: "high",
    status: "supported",
    source: "logic_reviewer",
    model: "后端配置读取中",
    problem: "Division helper does not handle a zero denominator.",
    impact: "Callers can receive ZeroDivisionError instead of a defined fallback or contract.",
    suggestion: "Define the zero-denominator contract and handle it before returning the division result.",
    file: "examples/sample_project/math_utils.py",
    line: "1-3",
    evidence: [
      {
        title: "静态判断",
        strength: "weak",
        summary: "logic_reviewer 在 diff 中发现 a / b 没有零分母处理。",
      },
      {
        title: "pytest 失败",
        strength: "strong",
        summary: "test_safe_divide_zero_denominator_returns_none 复现 ZeroDivisionError。",
        raw: "ZeroDivisionError: division by zero",
      },
    ],
  },
  {
    id: "finding_c3d4",
    severity: "low",
    status: "refuted",
    source: "security_maintainability_reviewer",
    model: "后端配置读取中",
    problem: "No clear security issue found in the bounded diff context.",
    impact: "The diff is too small to prove a security regression by itself.",
    suggestion: "Keep the helper covered by tests and avoid expanding scope without a caller contract.",
    file: "examples/sample_project/math_utils.py",
    line: "1-3",
    evidence: [
      {
        title: "定向复核",
        strength: "medium",
        summary: "安全 reviewer 未找到可支持的安全缺陷证据。",
      },
    ],
  },
];

const sourceTabs: { id: string; label: string; icon: IconComponent; disabled?: boolean }[] = [
  { id: "paste", label: "粘贴 Diff", icon: Clipboard },
  { id: "upload", label: "上传 .patch", icon: FileUp },
  { id: "branch", label: "Git 分支对比", icon: GitBranch, disabled: true },
  { id: "sample", label: "加载示例", icon: Play },
];

const statusLabels: Record<RunStatus, { label: string; tone: string; icon: IconComponent }> = {
  pending: { label: "等待中", tone: "neutral", icon: Circle },
  running: { label: "执行中", tone: "blue", icon: Loader2 },
  aborting: { label: "正在终止", tone: "orange", icon: Loader2 },
  succeeded: { label: "完成", tone: "green", icon: CheckCircle2 },
  failed: { label: "失败", tone: "red", icon: AlertTriangle },
  aborted: { label: "已终止", tone: "orange", icon: Square },
};

const apiEventTypes: ApiRunEventType[] = [
  "created",
  "running",
  "node",
  "succeeded",
  "failed",
  "abort_requested",
  "aborted",
];

function App() {
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const startsOnRun = pathParts[0] === "runs" && Boolean(pathParts[1]);
  const startsOnHistory = window.location.pathname === "/runs";
  const initialRunId = startsOnRun ? pathParts[1] : "abc123";
  const [view, setView] = useState<ViewName>(startsOnHistory ? "history" : startsOnRun ? "run" : "new");
  const [diffText, setDiffText] = useState(SAMPLE_DIFF);
  const [projectRoot, setProjectRoot] = useState("D:\\HaloWorkspace\\CodeCross");
  const [enabledReviewers, setEnabledReviewers] = useState<string[]>(
    reviewers.map((reviewer) => reviewer.id),
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxRounds, setMaxRounds] = useState(3);
  const [timeoutSeconds, setTimeoutSeconds] = useState(20);
  const [mockLlm, setMockLlm] = useState(false);
  const [runStatus, setRunStatus] = useState<RunStatus>(startsOnRun ? "running" : "pending");
  const [runId, setRunId] = useState(initialRunId);
  const [elapsed, setElapsed] = useState(0);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [logs, setLogs] = useState<LogRow[]>(startsOnRun ? [executionEvents[0]] : []);
  const [abortOpen, setAbortOpen] = useState(false);
  const [keepPartial, setKeepPartial] = useState(true);
  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  const [showLogHistory, setShowLogHistory] = useState(true);
  const [findingFilter, setFindingFilter] = useState<"all" | Severity>("all");
  const [apiMode, setApiMode] = useState(startsOnRun);
  const [apiRun, setApiRun] = useState<ApiRunRecord | null>(null);
  const [apiError, setApiError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [pollingFallback, setPollingFallback] = useState(false);
  const [runHistory, setRunHistory] = useState<ApiRunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [pendingApiEvents, setPendingApiEvents] = useState<ApiRunEvent[]>([]);
  const [runtimeConfig, setRuntimeConfig] = useState<ApiRuntimeConfig | null>(null);
  const [runtimeConfigError, setRuntimeConfigError] = useState("");
  const [reviewerOverrides, setReviewerOverrides] = useState<Record<string, ReviewerOverrideDraft>>({});
  const displayedApiEventIdsRef = useRef<Set<number>>(new Set());
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const parseResult = useMemo(() => parseDiff(diffText), [diffText]);
  const activeLog = logs[logs.length - 1];
  const reviewerOptions = useMemo(
    () => (runtimeConfig ? reviewersFromRuntimeConfig(runtimeConfig) : reviewers),
    [runtimeConfig],
  );
  const displayPipelineNodes = useMemo(
    () => applyRuntimeConfigToPipeline(pipelineNodes, runtimeConfig),
    [runtimeConfig],
  );
  // The visible active node should follow the newest backend event. Earlier V1.4
  // code used only the coarse `running` status, which made the UI jump straight
  // to Executor even though the backend was still in planner/context/reviewer.
  const eventActiveNode = displayPipelineNodes.find((node) => node.id === activeLog?.nodeId) ?? displayPipelineNodes[0];
  const activeNode = eventActiveNode;
  const activeNodeIndex = Math.max(0, nodeOrder.indexOf(activeNode.id));
  const normalizedFindings = useMemo(() => findingsFromSnapshot(apiRun?.snapshot), [apiRun?.snapshot]);
  const allFindings = apiRun?.snapshot ? normalizedFindings : sampleFindings;
  const visibleFindings = useMemo(() => {
    if (findingFilter === "all") return allFindings;
    return allFindings.filter((finding) => finding.severity === findingFilter);
  }, [allFindings, findingFilter]);
  const progress = useMemo(() => {
    if (apiMode) return apiProgressPercent(runStatus, activeNodeIndex);
    if (runStatus === "succeeded") return 100;
    if (runStatus === "aborted") return Math.max(28, Math.round((activeNodeIndex / nodeOrder.length) * 100));
    return Math.min(94, Math.round(((activeNodeIndex + 1) / nodeOrder.length) * 100));
  }, [activeNodeIndex, apiMode, runStatus]);

  const queueApiEvents = useCallback((events: ApiRunEvent[] | undefined) => {
    if (!events?.length) return;
    setPendingApiEvents((previous) => {
      const pendingIds = new Set(previous.map((event) => event.event_id));
      const nextEvents = events.filter(
        (event) => !pendingIds.has(event.event_id) && !displayedApiEventIdsRef.current.has(event.event_id),
      );
      return [...previous, ...nextEvents].sort((left, right) => left.event_id - right.event_id);
    });
  }, []);

  const updateReviewerOverride = useCallback(
    (reviewerId: string, patch: Partial<ReviewerOverrideDraft>) => {
      setReviewerOverrides((previous) => {
        const option = reviewerOptions.find((reviewer) => reviewer.id === reviewerId);
        const current =
          previous[reviewerId] ||
          defaultReviewerOverride(option || {
            id: reviewerId,
            name: reviewerId,
            provider: "",
            model: "",
            focus: "",
          });
        return { ...previous, [reviewerId]: { ...current, ...patch } };
      });
    },
    [reviewerOptions],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const loadRuntimeConfig = async () => {
        try {
          const response = await fetch(
            `/api/config/runtime?project_root=${encodeURIComponent(projectRoot)}`,
          );
          if (!response.ok) throw new Error(await readApiError(response));
          const config = (await response.json()) as ApiRuntimeConfig;
          setRuntimeConfig(config);
          setRuntimeConfigError("");
          setEnabledReviewers((previous) => {
            const available = new Set(config.reviewers.map((reviewer) => reviewer.id));
            const stillAvailable = previous.filter((reviewerId) => available.has(reviewerId));
            return stillAvailable.length ? stillAvailable : config.reviewers.map((reviewer) => reviewer.id);
          });
        } catch (error) {
          setRuntimeConfigError(error instanceof Error ? error.message : "读取后端模型配置失败。");
        }
      };

      void loadRuntimeConfig();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [projectRoot]);

  const startRun = useCallback(async () => {
    if (!parseResult.ok) return;
    setIsSubmitting(true);
    setApiError("");
    setPollingFallback(false);
    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          diff: diffText,
          project_root: projectRoot,
          mock_llm: mockLlm,
          reviewers: enabledReviewers,
          reviewer_overrides: buildReviewerOverridesPayload(reviewerOptions, reviewerOverrides),
          runner_config: {
            max_verification_rounds: maxRounds,
            test_timeout_seconds: timeoutSeconds,
          },
        }),
      });

      if (!response.ok) {
        throw new Error(await readApiError(response));
      }

      const record = (await response.json()) as ApiRunRecord;
      setApiMode(true);
      setApiRun(record);
      setRunId(record.run_id);
      // Do not immediately jump to a terminal/current node if the backend
      // already returned many events. Queue them and let the UI reveal progress.
      setRunStatus(record.events?.length ? "running" : mapApiStatus(record.status));
      setElapsed(0);
      setCurrentStepIndex(0);
      displayedApiEventIdsRef.current = new Set();
      setPendingApiEvents([]);
      setLogs(record.events?.length ? [] : logsFromApiRun(record));
      queueApiEvents(record.events);
      setView("run");
      window.history.pushState({}, "", `/runs/${record.run_id}`);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "创建 Run 失败。");
    } finally {
      setIsSubmitting(false);
    }
  }, [
    diffText,
    enabledReviewers,
    maxRounds,
    mockLlm,
    parseResult.ok,
    projectRoot,
    queueApiEvents,
    reviewerOptions,
    reviewerOverrides,
    timeoutSeconds,
  ]);

  const resetToNew = useCallback(() => {
    setView("new");
    setRunStatus("pending");
    setElapsed(0);
    setCurrentStepIndex(0);
    setLogs([]);
    setDrawer(null);
    setApiMode(false);
    setApiRun(null);
    setApiError("");
    setIsSubmitting(false);
    setPollingFallback(false);
    setPendingApiEvents([]);
    displayedApiEventIdsRef.current = new Set();
    window.history.pushState({}, "", "/new");
  }, []);

  const openHistory = useCallback(async () => {
    setView("history");
    setHistoryLoading(true);
    setHistoryError("");
    window.history.pushState({}, "", "/runs");
    try {
      const response = await fetch("/api/runs?limit=20");
      if (!response.ok) throw new Error(await readApiError(response));
      setRunHistory((await response.json()) as ApiRunSummary[]);
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "加载历史 Run 失败。");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const openHistoryRun = useCallback((summary: ApiRunSummary) => {
    setApiMode(true);
    setPollingFallback(true);
    setApiRun(null);
    setApiError("");
    setRunId(summary.run_id);
    setRunStatus(mapApiStatus(summary.status));
    setElapsed(0);
    setLogs([]);
    setPendingApiEvents([]);
    displayedApiEventIdsRef.current = new Set();
    setView("run");
    window.history.pushState({}, "", `/runs/${summary.run_id}`);
  }, []);

  useEffect(() => {
    if (startsOnHistory) void openHistory();
  }, [openHistory, startsOnHistory]);

  const copyRunId = useCallback(() => {
    void navigator.clipboard?.writeText(runId);
  }, [runId]);

  const confirmAbort = useCallback(() => {
    setAbortOpen(false);
    if (apiMode) {
      setRunStatus("aborting");
      setLogs((previous) =>
        appendLog(previous, {
          second: elapsed,
          nodeId: activeNode.id,
          actor: "Abort",
          kind: "abort",
          message: keepPartial ? "已向后端发送终止请求 · 保留已收集证据" : "已向后端发送终止请求",
        }),
      );
      void fetch(`/api/runs/${runId}/abort`, { method: "POST" })
        .then(async (response) => {
          if (!response.ok) throw new Error(await readApiError(response));
          const record = (await response.json()) as ApiRunRecord;
          setApiRun(record);
          queueApiEvents(record.events);
        })
        .catch((error) => {
          setApiError(error instanceof Error ? error.message : "终止请求失败。");
          setRunStatus("running");
        });
      return;
    }

    setRunStatus("aborting");
    setLogs((previous) => [
      ...previous,
      {
        second: elapsed,
        nodeId: activeNode.id,
        actor: "Abort",
        kind: "abort",
        message: keepPartial ? "收到终止信号 · 保留已收集证据并生成简报" : "收到终止信号",
      },
    ]);
    window.setTimeout(() => {
      setRunStatus("aborted");
      setLogs((previous) => [
        ...previous,
        {
          second: elapsed + 2,
          nodeId: "final_report",
          actor: "Report",
          kind: "done",
          message: "已终止 · 生成部分证据简报",
        },
      ]);
    }, 1200);
  }, [activeNode.id, apiMode, elapsed, keepPartial, queueApiEvents, runId]);

  useEffect(() => {
    if (apiMode || runStatus !== "running") return;
    const timer = window.setTimeout(() => {
      setElapsed((value) => value + 3);
      setCurrentStepIndex((index) => {
        const nextIndex = index + 1;
        if (nextIndex >= executionEvents.length) {
          setRunStatus("succeeded");
          return index;
        }
        setLogs((previous) => appendLog(previous, executionEvents[nextIndex]));
        return nextIndex;
      });
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [apiMode, currentStepIndex, runStatus]);

  useEffect(() => {
    if (!apiMode || (runStatus !== "pending" && runStatus !== "running")) return;
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [apiMode, runStatus]);

  useEffect(() => {
    if (!apiMode || view !== "run" || pendingApiEvents.length === 0) return;
    const event = pendingApiEvents[0];
    const timer = window.setTimeout(() => {
      displayedApiEventIdsRef.current.add(event.event_id);
      setPendingApiEvents((previous) => previous.slice(1));
      setLogs((previous) => appendApiEventLog(previous, event));

      if (event.type === "running" || event.type === "created" || event.type === "node") {
        setRunStatus("running");
      }
      if (event.type === "abort_requested") {
        setRunStatus("aborting");
      }
      if (event.type === "succeeded" || event.type === "failed" || event.type === "aborted") {
        setRunStatus(mapApiStatus(event.type));
      }
    }, 650);
    return () => window.clearTimeout(timer);
  }, [apiMode, pendingApiEvents, view]);

  useEffect(() => {
    if (!apiMode || view !== "run" || !runId) return;
    if (typeof EventSource === "undefined") {
      setPollingFallback(true);
      return;
    }

    let closedByClient = false;
    const source = new EventSource(`/api/runs/${runId}/events`);

    const refreshRun = async () => {
      const response = await fetch(`/api/runs/${runId}`);
      if (!response.ok) throw new Error(await readApiError(response));
      const record = (await response.json()) as ApiRunRecord;
      setApiRun(record);
      setApiError(record.error || "");
      queueApiEvents(record.events);
    };

    const handleEvent = (event: MessageEvent) => {
      const apiEvent = JSON.parse(event.data) as ApiRunEvent;
      queueApiEvents([apiEvent]);

      if (apiEvent.type === "succeeded" || apiEvent.type === "failed" || apiEvent.type === "aborted") {
        closedByClient = true;
        source.close();
        void refreshRun().catch((error) => {
          setApiError(error instanceof Error ? error.message : "刷新最终 Run 快照失败。");
          setPollingFallback(true);
        });
      }
    };

    apiEventTypes.forEach((type) => source.addEventListener(type, handleEvent as EventListener));
    source.onopen = () => setPollingFallback(false);
    source.onerror = () => {
      if (closedByClient) return;
      source.close();
      setApiError("事件流中断，已切换到轮询模式。");
      setPollingFallback(true);
    };

    return () => {
      closedByClient = true;
      source.close();
    };
  }, [apiMode, queueApiEvents, runId, view]);

  useEffect(() => {
    if (!apiMode || view !== "run" || !runId) return;

    let cancelled = false;
    const pollRun = async () => {
      try {
        const response = await fetch(`/api/runs/${runId}`);
        if (!response.ok) {
          throw new Error(await readApiError(response));
        }

        const record = (await response.json()) as ApiRunRecord;
        if (cancelled) return;

        const nextStatus = mapApiStatus(record.status);
        setApiRun(record);
        setApiError(record.error || "");
        if (!pollingFallback) {
          queueApiEvents(record.events);
          if (!record.events?.length) {
            setRunStatus(nextStatus);
            setLogs(logsFromApiRun(record));
          }
          return;
        }

        setRunStatus(nextStatus);
        setLogs(logsFromApiRun(record));
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : "轮询 Run 状态失败。";
        setApiError(message);
        setRunStatus("failed");
        setLogs(logsFromApiRun({ run_id: runId, status: "failed", created_at: "", updated_at: "", error: message }));
      }
    };

    // Polling remains as the snapshot/fallback path. SSE gives live logs, while
    // this request keeps the final structured report synchronized for panels.
    void pollRun();
    if (!pollingFallback && runStatus === "running") return () => {
      cancelled = true;
    };
    if (runStatus === "succeeded" || runStatus === "failed" || runStatus === "aborted") return () => {
      cancelled = true;
    };

    const timer = window.setInterval(pollRun, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [apiMode, pollingFallback, queueApiEvents, runId, runStatus, view]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && view === "new") {
        event.preventDefault();
        startRun();
      }
      if ((event.metaKey || event.ctrlKey) && event.key === "." && runStatus === "running") {
        event.preventDefault();
        setAbortOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [runStatus, startRun, view]);

  const handleUpload = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setDiffText(String(reader.result ?? ""));
    reader.readAsText(file);
  };

  return (
    <div className="app-shell">
      <Header onNew={resetToNew} onRuns={openHistory} />
      {view === "new" ? (
        <main className="workspace">
          <NewRunView
            diffText={diffText}
            enabledReviewers={enabledReviewers}
            maxRounds={maxRounds}
            mockLlm={mockLlm}
            parseResult={parseResult}
            projectRoot={projectRoot}
            reviewerOverrides={reviewerOverrides}
            reviewers={reviewerOptions}
            runtimeConfigError={runtimeConfigError}
            timeoutSeconds={timeoutSeconds}
            advancedOpen={advancedOpen}
            apiError={apiError}
            fileInputRef={fileInputRef}
            isSubmitting={isSubmitting}
            setAdvancedOpen={setAdvancedOpen}
            setDiffText={setDiffText}
            setEnabledReviewers={setEnabledReviewers}
            setMaxRounds={setMaxRounds}
            setMockLlm={setMockLlm}
            setProjectRoot={setProjectRoot}
            setTimeoutSeconds={setTimeoutSeconds}
            setReviewerOverride={updateReviewerOverride}
            onStart={startRun}
            onUpload={handleUpload}
          />
        </main>
      ) : view === "history" ? (
        <main className="workspace">
          <HistoryView
            error={historyError}
            loading={historyLoading}
            runs={runHistory}
            onNew={resetToNew}
            onOpenRun={openHistoryRun}
            onRefresh={openHistory}
          />
        </main>
      ) : (
        <main className="workspace run-workspace">
          <RunView
            activeNode={activeNode}
            currentStepIndex={activeNodeIndex}
            drawer={drawer}
            elapsed={elapsed}
            findingFilter={findingFilter}
            logs={logs}
            maxRounds={maxRounds}
            progress={progress}
            projectRoot={projectRoot}
            riskReport={apiRun?.snapshot?.evaluator_report}
            pipelineNodes={displayPipelineNodes}
            runId={runId}
            runError={apiError || apiRun?.error || ""}
            runStatus={runStatus}
            showLogHistory={showLogHistory}
            visibleFindings={visibleFindings}
            canAbort={runStatus === "pending" || runStatus === "running" || runStatus === "aborting"}
            onAbort={() => setAbortOpen(true)}
            onCopyRunId={copyRunId}
            onDrawer={setDrawer}
            onExportJson={() => (apiMode ? exportApiRun("json", runId) : exportRun("json", runId, logs))}
            onExportMarkdown={() => (apiMode ? exportApiRun("markdown", runId) : exportRun("markdown", runId, logs))}
            onFilter={setFindingFilter}
            onNew={resetToNew}
            onToggleLogHistory={() => setShowLogHistory((value) => !value)}
          />
        </main>
      )}
      <InspectorDrawer drawer={drawer} onClose={() => setDrawer(null)} />
      {abortOpen ? (
        <AbortDialog
          elapsed={elapsed}
          keepPartial={keepPartial}
          maxRounds={maxRounds}
          runId={runId}
          setKeepPartial={setKeepPartial}
          onCancel={() => setAbortOpen(false)}
          onConfirm={confirmAbort}
        />
      ) : null}
    </div>
  );
}

function Header({ onNew, onRuns }: { onNew: () => void; onRuns: () => void }) {
  return (
    <header className="topbar">
      <button className="brand" onClick={onNew} type="button">
        <span className="brand-mark">C</span>
        <span>CodeCross</span>
      </button>
      <nav className="nav-actions" aria-label="main navigation">
        <button type="button" onClick={onRuns}>
          <Activity size={15} />
          <span>Runs</span>
        </button>
        <button type="button">
          <Settings size={15} />
          <span>设置</span>
        </button>
        <button type="button">
          <BookOpen size={15} />
          <span>文档</span>
        </button>
      </nav>
    </header>
  );
}

function NewRunView(props: {
  advancedOpen: boolean;
  apiError: string;
  diffText: string;
  enabledReviewers: string[];
  fileInputRef: RefObject<HTMLInputElement | null>;
  isSubmitting: boolean;
  maxRounds: number;
  mockLlm: boolean;
  parseResult: ParseResult;
  projectRoot: string;
  reviewerOverrides: Record<string, ReviewerOverrideDraft>;
  reviewers: ReviewerOption[];
  runtimeConfigError: string;
  timeoutSeconds: number;
  setAdvancedOpen: (value: boolean) => void;
  setDiffText: (value: string) => void;
  setEnabledReviewers: (value: string[]) => void;
  setMaxRounds: (value: number) => void;
  setMockLlm: (value: boolean) => void;
  setProjectRoot: (value: string) => void;
  setTimeoutSeconds: (value: number) => void;
  setReviewerOverride: (reviewerId: string, patch: Partial<ReviewerOverrideDraft>) => void;
  onStart: () => void;
  onUpload: (file: File | undefined) => void;
}) {
  const toggleReviewer = (id: string) => {
    if (props.enabledReviewers.includes(id)) {
      props.setEnabledReviewers(props.enabledReviewers.filter((reviewerId) => reviewerId !== id));
      return;
    }
    props.setEnabledReviewers([...props.enabledReviewers, id]);
  };

  return (
    <section className="new-run page-surface">
      <div className="page-title-row">
        <div>
          <p className="eyebrow">New Run</p>
          <h1>新建审查</h1>
        </div>
        <div className="run-mode">
          <Activity size={16} />
          V1 真实模型审查流
        </div>
      </div>

      <div className="form-section">
        <div className="section-index">01</div>
        <div className="section-body">
          <h2>代码变更</h2>
          <div className="source-tabs" role="tablist" aria-label="diff source">
            {sourceTabs.map((tab) => {
              const Icon = tab.icon;
              const onClick =
                tab.id === "sample"
                  ? () => props.setDiffText(SAMPLE_DIFF)
                  : tab.id === "upload"
                    ? () => props.fileInputRef.current?.click()
                    : undefined;
              return (
                <button
                  className={tab.id === "paste" ? "active" : ""}
                  disabled={tab.disabled}
                  key={tab.id}
                  onClick={onClick}
                  type="button"
                >
                  <Icon size={15} />
                  {tab.label}
                </button>
              );
            })}
          </div>
          <input
            ref={props.fileInputRef}
            className="hidden-input"
            type="file"
            accept=".diff,.patch,text/plain"
            onChange={(event) => props.onUpload(event.target.files?.[0])}
          />
          <textarea
            className="diff-editor"
            spellCheck={false}
            value={props.diffText}
            onChange={(event) => props.setDiffText(event.target.value)}
          />
          <ParseStatus result={props.parseResult} />
        </div>
      </div>

      <div className="form-section">
        <div className="section-index">02</div>
        <div className="section-body">
          <h2>项目根目录</h2>
          <div className="path-row">
            <input
              value={props.projectRoot}
              onChange={(event) => props.setProjectRoot(event.target.value)}
            />
            <button type="button" title="选择项目根目录">
              <FolderOpen size={16} />
            </button>
          </div>
          <p className="field-hint">用作 pytest cwd 与 PYTHONPATH。</p>
        </div>
      </div>

      <div className="form-section">
        <div className="section-index">03</div>
        <div className="section-body">
          <h2>Reviewers</h2>
          <div className="reviewer-list">
            {props.reviewers.map((reviewer) => (
              <label className="reviewer-row" key={reviewer.id}>
                <input
                  type="checkbox"
                  checked={props.enabledReviewers.includes(reviewer.id)}
                  onChange={() => toggleReviewer(reviewer.id)}
                />
                <span className="reviewer-name">{reviewer.name}</span>
                <span className="reviewer-model">{reviewer.model}</span>
                <span className="reviewer-focus">{reviewer.focus}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="form-section compact">
        <div className="section-index">04</div>
        <div className="section-body">
          <button
            className="advanced-toggle"
            type="button"
            onClick={() => props.setAdvancedOpen(!props.advancedOpen)}
          >
            {props.advancedOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            高级选项
          </button>
          {props.advancedOpen ? (
            <>
              <div className="advanced-grid">
                <label>
                  <span>最大验证轮次</span>
                  <input
                    min={1}
                    max={8}
                    type="number"
                    value={props.maxRounds}
                    onChange={(event) => props.setMaxRounds(Number(event.target.value))}
                  />
                </label>
                <label>
                  <span>单测超时</span>
                  <input
                    min={5}
                    max={120}
                    type="number"
                    value={props.timeoutSeconds}
                    onChange={(event) => props.setTimeoutSeconds(Number(event.target.value))}
                  />
                </label>
                <label className="check-option">
                  <input
                    type="checkbox"
                    checked={props.mockLlm}
                    onChange={(event) => props.setMockLlm(event.target.checked)}
                  />
                  使用本地 Mock LLM（不调用真实模型）
                </label>
              </div>
              <ReviewerConnectionEditor
                overrides={props.reviewerOverrides}
                reviewers={props.reviewers}
                onChange={props.setReviewerOverride}
              />
            </>
          ) : null}
          {props.runtimeConfigError ? (
            <div className="parse-status error api-error">
              <AlertTriangle size={16} />
              <span>模型配置读取失败：{props.runtimeConfigError}</span>
            </div>
          ) : null}
        </div>
      </div>

      <div className="action-row">
        {props.apiError ? (
          <div className="parse-status error api-error">
            <AlertTriangle size={16} />
            <span>{props.apiError}</span>
          </div>
        ) : null}
        <button className="button ghost" type="button">
          取消
        </button>
        <button
          className="button primary"
          disabled={!props.parseResult.ok || props.enabledReviewers.length === 0 || props.isSubmitting}
          type="button"
          onClick={props.onStart}
        >
          {props.isSubmitting ? <Loader2 className="spin" size={16} /> : <Play size={16} fill="currentColor" />}
          {props.isSubmitting ? "创建中" : "开始审查"}
        </button>
      </div>
    </section>
  );
}

function HistoryView(props: {
  error: string;
  loading: boolean;
  runs: ApiRunSummary[];
  onNew: () => void;
  onOpenRun: (summary: ApiRunSummary) => void;
  onRefresh: () => void;
}) {
  return (
    <section className="history-page page-surface">
      <div className="page-title-row">
        <div>
          <p className="eyebrow">Runs</p>
          <h1>审查历史</h1>
        </div>
        <div className="run-actions">
          <button className="button compact-button" type="button" onClick={props.onRefresh}>
            {props.loading ? <Loader2 className="spin" size={15} /> : <Activity size={15} />}
            刷新
          </button>
          <button className="button primary compact-button" type="button" onClick={props.onNew}>
            <Play size={15} fill="currentColor" />
            新建
          </button>
        </div>
      </div>

      {props.error ? (
        <div className="parse-status error history-error">
          <AlertTriangle size={16} />
          <span>{props.error}</span>
        </div>
      ) : null}

      <div className="history-list">
        {props.runs.map((run) => {
          const StatusIcon = statusLabels[mapApiStatus(run.status)].icon;
          return (
            <button className="history-row" key={run.run_id} type="button" onClick={() => props.onOpenRun(run)}>
              <span className={`history-status tone-${statusLabels[mapApiStatus(run.status)].tone}`}>
                <StatusIcon size={16} />
                {statusLabels[mapApiStatus(run.status)].label}
              </span>
              <span className="history-main">
                <strong>#{run.run_id}</strong>
                <small>{run.project_root}</small>
              </span>
              <span className="history-metric">
                <strong>{run.risk_level.toUpperCase()}</strong>
                <small>{run.finding_count} findings</small>
              </span>
              <span className="history-time">{formatDateTime(run.updated_at)}</span>
            </button>
          );
        })}
        {!props.loading && props.runs.length === 0 ? (
          <div className="history-empty">
            <FileText size={18} />
            <span>暂无历史 Run。</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ReviewerConnectionEditor(props: {
  overrides: Record<string, ReviewerOverrideDraft>;
  reviewers: ReviewerOption[];
  onChange: (reviewerId: string, patch: Partial<ReviewerOverrideDraft>) => void;
}) {
  return (
    <section className="reviewer-config-panel">
      <div>
        <p className="eyebrow">Reviewer 连接配置</p>
        <h3>模型 / Base URL / API Key / A2A</h3>
      </div>
      <div className="reviewer-config-list">
        {props.reviewers.map((reviewer) => {
          const override = props.overrides[reviewer.id] || defaultReviewerOverride(reviewer);
          return (
            <article className="reviewer-config-card" key={reviewer.id}>
              <div className="reviewer-config-head">
                <strong>{reviewer.name}</strong>
                <span>{reviewer.focus}</span>
              </div>
              <div className="reviewer-config-grid">
                <label>
                  <span>Provider</span>
                  <input
                    value={override.provider}
                    onChange={(event) => props.onChange(reviewer.id, { provider: event.target.value })}
                  />
                </label>
                <label>
                  <span>Model</span>
                  <input
                    value={override.model}
                    onChange={(event) => props.onChange(reviewer.id, { model: event.target.value })}
                  />
                </label>
                <label>
                  <span>Base URL</span>
                  <input
                    placeholder="留空则使用 .env"
                    value={override.baseUrl}
                    onChange={(event) => props.onChange(reviewer.id, { baseUrl: event.target.value })}
                  />
                </label>
                <label>
                  <span>API Key</span>
                  <input
                    placeholder="仅本次 Run 使用，不落库"
                    type="password"
                    value={override.apiKey}
                    onChange={(event) => props.onChange(reviewer.id, { apiKey: event.target.value })}
                  />
                </label>
              </div>
              <label className="check-option reviewer-a2a-toggle">
                <input
                  type="checkbox"
                  checked={override.useA2a}
                  onChange={(event) => props.onChange(reviewer.id, { useA2a: event.target.checked })}
                />
                通过 A2A reviewer 服务调用
              </label>
              {override.useA2a ? (
                <label className="reviewer-a2a-url">
                  <span>A2A JSON-RPC URL</span>
                  <input
                    placeholder="http://127.0.0.1:9100/a2a"
                    value={override.a2aUrl}
                    onChange={(event) => props.onChange(reviewer.id, { a2aUrl: event.target.value })}
                  />
                </label>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function RunView(props: {
  activeNode: PipelineNode;
  canAbort: boolean;
  currentStepIndex: number;
  drawer: DrawerState | null;
  elapsed: number;
  findingFilter: "all" | Severity;
  logs: LogRow[];
  maxRounds: number;
  pipelineNodes: PipelineNode[];
  progress: number;
  projectRoot: string;
  riskReport?: ApiEvaluatorReport;
  runId: string;
  runError: string;
  runStatus: RunStatus;
  showLogHistory: boolean;
  visibleFindings: Finding[];
  onAbort: () => void;
  onCopyRunId: () => void;
  onDrawer: (drawer: DrawerState | null) => void;
  onExportJson: () => void;
  onExportMarkdown: () => void;
  onFilter: (filter: "all" | Severity) => void;
  onNew: () => void;
  onToggleLogHistory: () => void;
}) {
  const StatusIcon = statusLabels[props.runStatus].icon;
  const isTerminal =
    props.runStatus === "succeeded" || props.runStatus === "aborted" || props.runStatus === "failed";
  const canExport = props.runStatus === "succeeded" || props.runStatus === "aborted";
  const roundLabel =
    props.currentStepIndex < 7
      ? `Round 1 / ${props.maxRounds}`
      : `Round ${Math.min(props.maxRounds, 2)} / ${props.maxRounds}`;

  return (
    <section className="run-page page-surface">
      <div className="run-heading">
        <div>
          <p className="breadcrumb">CodeCross / Run #{props.runId}</p>
          <div className={`status-line tone-${statusLabels[props.runStatus].tone}`}>
            <StatusIcon className={props.runStatus === "running" || props.runStatus === "aborting" ? "spin" : ""} size={18} />
            <strong>{statusLabels[props.runStatus].label}</strong>
            <span>耗时 {formatElapsed(props.elapsed)}</span>
            {!isTerminal ? <span>{roundLabel}</span> : null}
            <span>V1.3 API</span>
          </div>
        </div>
        <div className="run-actions">
          {isTerminal ? (
            <>
              <button className="button compact-button" type="button" onClick={props.onNew}>
                <RotateCcw size={15} />
                新建
              </button>
              {canExport ? (
                <>
                  <button className="button compact-button" type="button" onClick={props.onExportMarkdown}>
                    <FileText size={15} />
                    导出 MD
                  </button>
                  <button className="button compact-button" type="button" onClick={props.onExportJson}>
                    <FileJson size={15} />
                    导出 JSON
                  </button>
                </>
              ) : null}
            </>
          ) : (
            <>
              <button className="button compact-button" type="button" onClick={props.onCopyRunId}>
                <Copy size={15} />
                复制 ID
              </button>
              <button
                className="button abort-button"
                disabled={props.runStatus === "aborting" || !props.canAbort}
                type="button"
                onClick={props.onAbort}
                title={props.canAbort ? "终止当前 Run" : "V1.4 会接入真实后端终止"}
              >
                {props.runStatus === "aborting" ? <Loader2 className="spin" size={15} /> : <Square size={15} />}
                {props.canAbort ? (props.runStatus === "aborting" ? "正在终止" : "终止") : "V1.4 终止"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="progress-row">
        <div className="progress-track">
          <span style={{ width: `${props.progress}%` }} />
        </div>
        <strong>{props.progress}%</strong>
      </div>

      {props.runStatus === "failed" ? (
        <FailureSummary error={props.runError} />
      ) : isTerminal ? (
        <RiskSummary report={props.riskReport} status={props.runStatus} />
      ) : (
        <CurrentStepCard elapsed={props.elapsed} node={props.activeNode} runStatus={props.runStatus} />
      )}

      {isTerminal && props.runStatus !== "failed" ? (
        <FindingsPanel
          findings={props.visibleFindings}
          filter={props.findingFilter}
          onDrawer={(finding) => props.onDrawer({ type: "finding", finding })}
          onFilter={props.onFilter}
        />
      ) : null}

      <PipelineView
        currentStepIndex={props.currentStepIndex}
        pipelineNodes={props.pipelineNodes}
        runStatus={props.runStatus}
        selectedNode={props.drawer?.type === "node" ? props.drawer.node.id : ""}
        onNode={(node, status) => props.onDrawer({ type: "node", node, status })}
      />

      <LogPanel
        logs={props.logs}
        showLogHistory={props.showLogHistory}
        onToggle={props.onToggleLogHistory}
      />

      {!isTerminal ? (
        <div className="run-accordions">
          <button type="button">
            <ChevronDown size={15} />
            Reviewer 已发现 ({props.visibleFindings.length})
          </button>
          <button type="button">
            <ChevronDown size={15} />
            已生成测试 ({props.currentStepIndex >= 9 ? 1 : 0})
          </button>
          <div className="request-snapshot">
            <Terminal size={16} />
            <span>{props.projectRoot}</span>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ParseStatus({ result }: { result: ParseResult }) {
  return (
    <div className={`parse-status ${result.ok ? "ok" : "error"}`}>
      {result.ok ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
      <span>{result.message}</span>
    </div>
  );
}

function CurrentStepCard({
  elapsed,
  node,
  runStatus,
}: {
  elapsed: number;
  node: PipelineNode;
  runStatus: RunStatus;
}) {
  const isAborting = runStatus === "aborting";
  return (
    <section className={`current-step ${isAborting ? "aborting" : ""}`}>
      <div className="section-header">
        <div>
          <p className="eyebrow">当前步骤</p>
          <h2>{isAborting ? "正在终止当前 Run" : `${node.label} · ${node.friendly}`}</h2>
        </div>
        {isAborting ? <AlertTriangle size={18} /> : <Loader2 className="spin" size={18} />}
      </div>
      <p>{isAborting ? "保留已收集证据，等待简报收尾。" : node.detail}</p>
      <div className="step-meta">
        <span>目标: finding_a1b2 · safe_divide</span>
        <span>模型: {node.model}</span>
        <span>已耗时 {formatElapsed(elapsed)}</span>
      </div>
    </section>
  );
}

function FailureSummary({ error }: { error: string }) {
  return (
    <section className="risk-summary failed">
      <div>
        <p className="eyebrow">运行失败</p>
        <h2>
          <AlertTriangle size={18} />
          FAILED
        </h2>
      </div>
      <div className="risk-copy">
        <strong>后端未能生成最终报告</strong>
        <p>{error || "Run 执行失败，请查看后端终端日志或稍后重试。"}</p>
        <span>建议: 先确认 API 服务、项目路径、依赖和真实模型 API Key 配置。</span>
      </div>
    </section>
  );
}

function RiskSummary({ report, status }: { report?: ApiEvaluatorReport; status: RunStatus }) {
  const aborted = status === "aborted";
  const riskLevel = (report?.risk_level || (aborted ? "partial" : "high")).toUpperCase();
  const confidence = report?.confidence || (aborted ? "medium" : "high");
  const summary =
    report?.evidence_summary ||
    (aborted
      ? "Run 已终止，但已收集证据足以生成部分简报。"
      : "A high severity finding has supporting test evidence.");
  const action = report?.recommended_action || "Block or request changes before merge.";

  return (
    <section className={`risk-summary ${aborted ? "partial" : ""}`}>
      <div>
        <p className="eyebrow">风险评估</p>
        <h2>
          <AlertTriangle size={18} />
          {riskLevel}
        </h2>
      </div>
      <div className="risk-copy">
        <strong>置信度: {confidence}</strong>
        <p>{summary}</p>
        <span>建议: {action}</span>
      </div>
    </section>
  );
}

function PipelineView({
  currentStepIndex,
  pipelineNodes,
  runStatus,
  selectedNode,
  onNode,
}: {
  currentStepIndex: number;
  pipelineNodes: PipelineNode[];
  runStatus: RunStatus;
  selectedNode: string;
  onNode: (node: PipelineNode, status: NodeStatus) => void;
}) {
  const activeNodeOrder = pipelineNodes.map((node) => node.id);
  const nodeStatus = (nodeId: string): NodeStatus => {
    const index = activeNodeOrder.indexOf(nodeId);
    if (runStatus === "succeeded") return "done";
    if (runStatus === "failed") return index === currentStepIndex ? "failed" : index < currentStepIndex ? "done" : "pending";
    if (runStatus === "aborted") return index <= currentStepIndex ? "done" : "pending";
    if (index < currentStepIndex) return "done";
    if (index === currentStepIndex) return "running";
    return "pending";
  };

  return (
    <section className="pipeline-block">
      <div className="section-header">
        <div>
          <p className="eyebrow">Pipeline</p>
          <h2>{runStatus === "succeeded" ? "已完成" : "节点级进度"}</h2>
        </div>
        <button className="icon-note" type="button">
          <ShieldCheck size={16} />
          固定拓扑
        </button>
      </div>
      <div className="pipeline-scroller">
        <div className="pipeline-stage">
          <svg className="pipeline-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <path d="M16 21 L24 21 L24 12 L25 12" />
            <path d="M16 21 L24 21 L24 31 L25 31" />
            <path d="M39 12 L49 12 L49 21 L48 21" />
            <path d="M39 31 L49 31 L49 21 L48 21" />
            <path d="M64 21 L68 21" />
            <path d="M83 21 L92 21 L92 43 L9 43 L9 61 L7 61" />
            <path d="M23 61 L27 61" />
            <path d="M41 61 L45 61" />
            <path d="M59 61 L63 61" />
            <path d="M77 61 L81 61" />
            <path d="M88 70 L88 78 L43 78 L43 80" />
            <path d="M51 86 L56 86" />
            <path d="M72 86 L77 86" />
          </svg>
          {pipelineNodes.map((node) => {
            const status = nodeStatus(node.id);
            return (
              <button
                className={`pipeline-node ${status} ${selectedNode === node.id ? "selected" : ""}`}
                key={node.id}
                style={{ left: `${node.x}%`, top: `${node.y}%` }}
                type="button"
                onClick={() => onNode(node, status)}
              >
                <NodeIcon status={status} />
                <span>{node.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function NodeIcon({ status }: { status: NodeStatus }) {
  if (status === "done") return <CheckCircle2 size={14} />;
  if (status === "running") return <Loader2 className="spin" size={14} />;
  if (status === "failed") return <AlertTriangle size={14} />;
  return <Circle size={14} />;
}

function FindingsPanel({
  findings,
  filter,
  onDrawer,
  onFilter,
}: {
  findings: Finding[];
  filter: "all" | Severity;
  onDrawer: (finding: Finding) => void;
  onFilter: (filter: "all" | Severity) => void;
}) {
  return (
    <section className="findings-panel">
      <div className="section-header">
        <div>
          <p className="eyebrow">Findings</p>
          <h2>{findings.length} 条结论</h2>
        </div>
        <div className="filter-actions">
          <select value={filter} onChange={(event) => onFilter(event.target.value as "all" | Severity)}>
            <option value="all">全部</option>
            <option value="critical">critical</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
          <select defaultValue="severity">
            <option value="severity">按严重度排序</option>
            <option value="status">按验证状态排序</option>
          </select>
        </div>
      </div>
      <div className="finding-list">
        {findings.map((finding) => (
          <article className="finding-row" key={finding.id}>
            <div className="finding-severity">
              <span className={`severity-dot ${finding.severity}`} />
              <strong className={`severity-text ${finding.severity}`}>{finding.severity.toUpperCase()}</strong>
            </div>
            <div className="finding-main">
              <div>
                <strong>{finding.status}</strong>
                <span>{finding.id}</span>
              </div>
              <p>{finding.problem}</p>
              <small>
                {finding.source} · {finding.file}:{finding.line}
              </small>
            </div>
            <button className="button compact-button" type="button" onClick={() => onDrawer(finding)}>
              <Eye size={15} />
              查看
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

function LogPanel({
  logs,
  showLogHistory,
  onToggle,
}: {
  logs: LogRow[];
  showLogHistory: boolean;
  onToggle: () => void;
}) {
  const logText = logs.map((log) => `${formatElapsed(log.second)} ${log.actor} ${logIcon(log.kind)} ${log.message}`).join("\n");
  const visibleLogs = showLogHistory ? logs : logs.slice(-5);
  return (
    <section className="log-panel">
      <div className="section-header">
        <div>
          <p className="eyebrow">执行日志</p>
          <h2>{logs.length} 条事件</h2>
        </div>
        <div className="filter-actions">
          <button className="button compact-button" type="button" onClick={onToggle}>
            {showLogHistory ? "折叠" : "展开全部"}
          </button>
          <button className="button compact-button" type="button" onClick={() => void navigator.clipboard?.writeText(logText)}>
            <Copy size={15} />
            复制
          </button>
        </div>
      </div>
      <div className="log-list">
        {visibleLogs.map((log, index) => (
          <div className="log-row" key={`${log.nodeId}-${log.second}-${index}`}>
            <span>{formatElapsed(log.second)}</span>
            <strong>{log.actor}</strong>
            <i className={`log-kind ${log.kind}`}>{logIcon(log.kind)}</i>
            <p>{log.message}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function InspectorDrawer({ drawer, onClose }: { drawer: DrawerState | null; onClose: () => void }) {
  if (!drawer) return null;
  return (
    <aside className="drawer" aria-label="detail drawer">
      <div className="drawer-header">
        <div>
          <p className="eyebrow">{drawer.type === "node" ? "Node" : "Finding"}</p>
          <h2>{drawer.type === "node" ? drawer.node.label : drawer.finding.id}</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} title="关闭">
          <X size={17} />
        </button>
      </div>
      {drawer.type === "node" ? <NodeDrawer drawer={drawer} /> : <FindingDrawer finding={drawer.finding} />}
    </aside>
  );
}

function NodeDrawer({ drawer }: { drawer: Extract<DrawerState, { type: "node" }> }) {
  const payload = {
    node: drawer.node.id,
    friendly_name: drawer.node.friendly,
    status: drawer.status,
    model: drawer.node.model,
    input: {
      diff: "examples/sample_project/math_utils.py",
      target: drawer.node.id.includes("reviewer") ? "review_plan" : "state_snapshot",
    },
    output: {
      summary: drawer.node.detail,
      evidence_ids: drawer.node.id === "executor" ? ["evidence_pytest_zero_denominator"] : [],
    },
  };
  return (
    <div className="drawer-body">
      <div className="meta-grid">
        <span>状态</span>
        <strong>{drawer.status}</strong>
        <span>模型</span>
        <strong>{drawer.node.model}</strong>
      </div>
      <h3>{drawer.node.friendly}</h3>
      <p>{drawer.node.detail}</p>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </div>
  );
}

function FindingDrawer({ finding }: { finding: Finding }) {
  return (
    <div className="drawer-body">
      <div className="finding-drawer-title">
        <strong className={`severity-text ${finding.severity}`}>▲ {finding.severity.toUpperCase()}</strong>
        <span>{finding.status}</span>
        <span>{finding.source} ({finding.model})</span>
      </div>
      <h3>问题</h3>
      <p>{finding.problem}</p>
      <h3>影响</h3>
      <p>{finding.impact}</p>
      <h3>证据链</h3>
      <div className="evidence-list">
        {finding.evidence.map((item, index) => (
          <article className="evidence-item" key={item.title}>
            <strong>
              {index + 1}. {item.title} · {item.strength}
            </strong>
            <p>{item.summary}</p>
            {item.raw ? <pre>{item.raw}</pre> : null}
          </article>
        ))}
      </div>
      <h3>建议</h3>
      <p>{finding.suggestion}</p>
      <h3>代码位置</h3>
      <button
        className="button compact-button"
        type="button"
        onClick={() => void navigator.clipboard?.writeText(`${finding.file}:${finding.line}`)}
      >
        <Copy size={15} />
        {finding.file}:{finding.line}
      </button>
    </div>
  );
}

function AbortDialog({
  elapsed,
  keepPartial,
  maxRounds,
  runId,
  setKeepPartial,
  onCancel,
  onConfirm,
}: {
  elapsed: number;
  keepPartial: boolean;
  maxRounds: number;
  runId: string;
  setKeepPartial: (value: boolean) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="abort-modal" role="dialog" aria-modal="true" aria-labelledby="abort-title">
        <div className="modal-title">
          <AlertTriangle size={18} />
          <h2 id="abort-title">终止当前 Run?</h2>
        </div>
        <p>
          Run #{runId} 已运行 {formatElapsed(elapsed)}（Round 1 / {maxRounds}）。
        </p>
        <p>已完成 reviewer 输出和至少一轮验证证据采集。</p>
        <label className="check-option keep-partial">
          <input
            type="checkbox"
            checked={keepPartial}
            onChange={(event) => setKeepPartial(event.target.checked)}
          />
          保留已收集证据并生成简报
        </label>
        <p className="modal-warning">终止后无法恢复，是否继续?</p>
        <div className="modal-actions">
          <button className="button ghost" type="button" onClick={onCancel}>
            取消
          </button>
          <button className="button danger" type="button" onClick={onConfirm}>
            确认终止
          </button>
        </div>
      </section>
    </div>
  );
}

function mapApiStatus(status: ApiRunStatus): RunStatus {
  // Keep the API/UI boundary explicit even when the string values currently
  // match; future backend states can be mapped here without touching components.
  return status;
}

function reviewerModelLabel(reviewer: ApiRuntimeReviewer) {
  return `${reviewer.provider}/${reviewer.model}`;
}

function reviewersFromRuntimeConfig(config: ApiRuntimeConfig): ReviewerOption[] {
  // The browser must display the same reviewer models the backend will call.
  // This avoids stale hard-coded UI labels such as old Claude/Sonnet defaults.
  return config.reviewers.map((reviewer) => ({
    id: reviewer.id,
    name: reviewer.name,
    provider: reviewer.provider,
    model: reviewerModelLabel(reviewer),
    focus: reviewer.focus || "configured reviewer",
  }));
}

function splitModelLabel(label: string) {
  const [provider, ...modelParts] = label.split("/");
  return { provider: provider || "", model: modelParts.join("/") || label };
}

function defaultReviewerOverride(reviewer: ReviewerOption): ReviewerOverrideDraft {
  return {
    provider: reviewer.provider,
    model: splitModelLabel(reviewer.model).model,
    baseUrl: "",
    apiKey: "",
    useA2a: false,
    a2aUrl: "http://127.0.0.1:9100/a2a",
  };
}

function buildReviewerOverridesPayload(
  reviewers: ReviewerOption[],
  overrides: Record<string, ReviewerOverrideDraft>,
) {
  // API keys are sent only in this create-run request. Backend schemas exclude
  // them from response serialization and SQLite persistence.
  return Object.fromEntries(
    reviewers.map((reviewer) => {
      const override = overrides[reviewer.id] || defaultReviewerOverride(reviewer);
      return [
        reviewer.id,
        {
          provider: override.provider.trim() || reviewer.provider,
          model: override.model.trim() || splitModelLabel(reviewer.model).model,
          base_url: override.baseUrl.trim() || undefined,
          api_key: override.apiKey.trim() || undefined,
          use_a2a: override.useA2a,
          a2a_url: override.a2aUrl.trim() || undefined,
        },
      ];
    }),
  );
}

function modelForPipelineNode(node: PipelineNode, config: ApiRuntimeConfig | null) {
  if (!config) return node.model;
  if (node.id.startsWith("reviewer_")) {
    const reviewer = config.reviewers.find((item) => node.id.endsWith(item.id));
    return reviewer ? reviewerModelLabel(reviewer) : node.model;
  }
  const roleByNode: Record<string, string> = {
    planner: "planner",
    test_strategy: "test_strategy_agent",
    test_case: "test_case_agent",
    test_author: "test_author_agent",
    test_result_analyzer: "test_result_analyzer",
    coverage_gap: "coverage_gap_agent",
    evaluator: "evaluator",
    high_risk_review: "high_risk_reviewer",
  };
  const role = roleByNode[node.id];
  return role ? config.role_models[role] || node.model : node.model;
}

function applyRuntimeConfigToPipeline(nodes: PipelineNode[], config: ApiRuntimeConfig | null) {
  return nodes.map((node) => ({ ...node, model: modelForPipelineNode(node, config) }));
}

function apiProgressPercent(status: RunStatus, activeNodeIndex: number) {
  // V1.4+ uses live backend events for the active node. The percentage remains
  // approximate, but it now advances with the latest event instead of jumping to
  // Executor whenever the run-level status is merely "running".
  if (status === "succeeded" || status === "failed" || status === "aborted") return 100;
  if (status === "running" || status === "aborting") {
    return Math.min(94, Math.max(8, Math.round(((activeNodeIndex + 1) / nodeOrder.length) * 100)));
  }
  return 8;
}

function normalizeSeverity(value: string | undefined): Severity {
  const allowed: Severity[] = ["critical", "high", "medium", "low"];
  return allowed.includes(value as Severity) ? (value as Severity) : "medium";
}

function normalizeVerificationStatus(value: string | undefined): VerificationStatus {
  const allowed: VerificationStatus[] = ["supported", "refuted", "inconclusive", "unverified"];
  if (allowed.includes(value as VerificationStatus)) return value as VerificationStatus;
  return value === "not_testable" ? "inconclusive" : "unverified";
}

function normalizeEvidenceStrength(value: string | undefined): EvidenceItem["strength"] {
  const allowed: EvidenceItem["strength"][] = ["weak", "medium", "strong"];
  return allowed.includes(value as EvidenceItem["strength"]) ? (value as EvidenceItem["strength"]) : "medium";
}

function formatApiLineRange(lineRange: ApiFinding["line_range"]) {
  if (!lineRange?.start && !lineRange?.end) return "-";
  if (lineRange.start && lineRange.end && lineRange.start !== lineRange.end) {
    return `${lineRange.start}-${lineRange.end}`;
  }
  return String(lineRange.start ?? lineRange.end ?? "-");
}

function evidenceForFinding(snapshot: ApiSnapshot, findingId: string, finding: ApiFinding): EvidenceItem[] {
  const board = snapshot.board_summary;
  const verification = board?.finding_verifications?.[findingId];
  const evidenceMap = board?.evidence_map ?? {};

  const linkedEvidence =
    verification?.evidence_ids
      ?.map((evidenceId) => evidenceMap[evidenceId])
      .filter((item): item is ApiEvidenceItem => Boolean(item))
      .map((item) => ({
        title: item.evidence_type || item.source || "Evidence",
        strength: normalizeEvidenceStrength(item.strength),
        summary: item.summary || "后端返回了一条证据记录。",
        raw: item.raw_reference,
      })) ?? [];

  if (linkedEvidence.length > 0) return linkedEvidence;
  if (finding.evidence) {
    return [{ title: "Reviewer evidence", strength: "weak", summary: finding.evidence }];
  }
  return [{ title: "Board status", strength: "medium", summary: verification?.reason || "暂无可展示证据。" }];
}

function findingsFromSnapshot(snapshot: ApiSnapshot | null | undefined): Finding[] {
  if (!snapshot?.findings) return [];

  // Backend Finding uses the Python schema names; the UI normalizes them into a
  // compact card shape so rendering code can stay presentation-focused.
  return snapshot.findings.map((finding, index) => {
    const id = finding.finding_id || `finding_${index + 1}`;
    const verification = snapshot.board_summary?.finding_verifications?.[id];
    return {
      id,
      severity: normalizeSeverity(finding.severity),
      status: normalizeVerificationStatus(verification?.status),
      source: finding.source_reviewer || "reviewer",
      model: finding.model || "model",
      problem: finding.problem || "No problem text returned.",
      impact: finding.impact || "No impact text returned.",
      suggestion: finding.suggestion || "No suggestion returned.",
      file: finding.file_path || "-",
      line: formatApiLineRange(finding.line_range),
      evidence: evidenceForFinding(snapshot, id, finding),
    };
  });
}

function nodeIdFromApiEvent(event: ApiRunEvent) {
  const node = event.node || "workflow";
  if (node === "reviewers") return "reviewer_0_logic_reviewer";
  if (node === "context_retriever" || node === "workflow" || node === "api") return "planner";
  return nodeOrder.includes(node) ? node : "final_report";
}

function logKindFromApiEvent(event: ApiRunEvent): LogKind {
  if (event.type === "succeeded") return "done";
  if (event.type === "failed") return "failed";
  if (event.type === "aborted" || event.type === "abort_requested") return "abort";
  if (event.type === "created") return "start";
  return "running";
}

function logFromApiEvent(event: ApiRunEvent, index: number): LogRow {
  return {
    second: index,
    nodeId: nodeIdFromApiEvent(event),
    actor: event.type,
    kind: logKindFromApiEvent(event),
    message: event.message,
  };
}

function appendApiEventLog(previous: LogRow[], event: ApiRunEvent) {
  const next = logFromApiEvent(event, Math.max(previous.length, event.event_id - 1));
  return appendLog(previous, next);
}

function logsFromApiRun(record: ApiRunRecord): LogRow[] {
  if (record.events?.length) {
    return record.events.map((event, index) => logFromApiEvent(event, index));
  }

  const status = mapApiStatus(record.status);
  const snapshot = record.snapshot;
  const findingCount = snapshot?.board_summary?.finding_count ?? snapshot?.findings?.length ?? 0;
  const failedCount = snapshot?.board_summary?.failed_count ?? 0;
  const risk = snapshot?.evaluator_report?.risk_level ?? "unknown";

  if (status === "pending") {
    return [
      {
        second: 0,
        nodeId: "planner",
        actor: "API",
        kind: "start",
        message: "Run 已创建，等待后端调度。",
      },
    ];
  }

  if (status === "running") {
    return [
      {
        second: 0,
        nodeId: "planner",
        actor: "API",
        kind: "running",
        message: "后端正在执行真实 workflow，等待事件流返回节点进度。",
      },
    ];
  }

  if (status === "failed") {
    return [
      executionEvents[0],
      {
        second: 1,
        nodeId: "final_report",
        actor: "API",
        kind: "failed",
        message: record.error || "后端执行失败。",
      },
    ];
  }

  if (status === "aborted") {
    return [
      executionEvents[0],
      {
        second: 1,
        nodeId: "final_report",
        actor: "Abort",
        kind: "abort",
        message: record.error || "Run 已终止。",
      },
    ];
  }

  return [
    ...executionEvents,
    {
      second: 54,
      nodeId: "final_report",
      actor: "API",
      kind: "done",
      message: `真实 workflow 完成 · ${findingCount} findings · ${failedCount} failed tests · risk ${risk}`,
    },
  ];
}

async function readApiError(response: Response) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") return payload.detail;
    return JSON.stringify(payload.detail ?? payload);
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

function parseDiff(diffText: string): ParseResult {
  const trimmed = diffText.trim();
  if (!trimmed) {
    return {
      ok: false,
      message: "请先粘贴 unified diff。",
      files: [],
      symbols: [],
      additions: 0,
    };
  }

  const files = Array.from(trimmed.matchAll(/^diff --git a\/(.+?) b\/(.+)$/gm)).map((match) => match[2]);
  const functions = Array.from(trimmed.matchAll(/^\+(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(/gm)).map((match) => match[1]);
  const classes = Array.from(trimmed.matchAll(/^\+class\s+([A-Za-z_][\w]*)/gm)).map((match) => match[1]);
  const symbols = [...new Set([...functions, ...classes])];
  const additions = Array.from(trimmed.matchAll(/^\+(?!\+\+|\s*$)/gm)).length;

  if (files.length === 0) {
    return {
      ok: false,
      message: "未检测到 diff --git 文件头。",
      files: [],
      symbols,
      additions,
    };
  }

  return {
    ok: true,
    message: `解析成功 · ${files.length} 个文件 · ${additions} 个新增行 · ${symbols.length || 0} 个新增符号`,
    files,
    symbols,
    additions,
  };
}

function logIcon(kind: LogKind) {
  if (kind === "done") return "✓";
  if (kind === "failed") return "✗";
  if (kind === "abort") return "■";
  if (kind === "running") return "⟳";
  return "▶";
}

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const rest = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${minutes}:${rest}`;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "-";
  return date.toLocaleString();
}

function exportRun(format: "json" | "markdown", runId: string, logs: LogRow[]) {
  const payload = {
    run_id: runId,
    status: "succeeded",
    risk_level: "high",
    findings: sampleFindings,
    logs,
  };
  const content =
    format === "json"
      ? JSON.stringify(payload, null, 2)
      : `# CodeCross V1 Report\n\nRisk Level: high\n\n${sampleFindings
          .map((finding) => `- ${finding.severity.toUpperCase()} ${finding.id}: ${finding.problem}`)
          .join("\n")}\n`;
  const blob = new Blob([content], { type: format === "json" ? "application/json" : "text/markdown" });
  downloadBlob(blob, `codecross_${runId}.${format === "json" ? "json" : "md"}`);
}

async function exportApiRun(format: "json" | "markdown", runId: string) {
  try {
    const response = await fetch(`/api/runs/${runId}/export?format=${format}`);
    if (!response.ok) {
      throw new Error(await readApiError(response));
    }
    const blob = await response.blob();
    downloadBlob(blob, `codecross_${runId}.${format === "json" ? "json" : "md"}`);
  } catch (error) {
    window.alert(error instanceof Error ? error.message : "导出失败。");
  }
}

function downloadBlob(blob: Blob, fileName: string) {
  // Keeping download behavior in one helper prevents the mock exporter and real
  // API exporter from drifting apart.
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default App;

