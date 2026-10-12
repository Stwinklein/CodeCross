from __future__ import annotations

"""离线评测使用的稳定数据契约。

业务运行时的 ``Finding`` 带有 reviewer、evidence id 等大量过程字段；评测只关心
“系统最终声称发现了什么”。因此这里使用更小的模型，把运行时 schema 与评测集
schema 解耦。公开 benchmark 字段变化时只需要改适配器，不会污染主工作流。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import RiskLevel, Severity


class EvaluationModel(BaseModel):
    """评测模型公共基类，禁止静默吞掉拼错的字段。"""

    model_config = ConfigDict(extra="forbid")


class ReferenceFinding(EvaluationModel):
    """一个人工确认或 benchmark 提供的标准问题。"""

    description: str
    comment_id: str = ""
    severity: Severity | None = None
    category: str = ""
    file_path: str = ""
    line_start: int | None = None
    line_end: int | None = None
    # keywords 主要服务于完全离线的确定性匹配。公开 benchmark 的自然语言评论
    # 通常没有关键词，此时可改用 LLM semantic judge，但基础 scorer 仍然可运行。
    keywords: list[str] = Field(default_factory=list)


class PredictedFinding(EvaluationModel):
    """从 CodeCross 运行结果中抽取的、供 evaluator 消费的精简 finding。"""

    description: str
    severity: Severity
    category: str = ""
    # 保留来源才能分别衡量每个 Reviewer 的贡献、失败率和误报倾向。旧评测输出没有
    # 这两个字段，因此使用空字符串保持向后兼容；Judge 的匹配逻辑也不依赖来源。
    source_reviewer: str = ""
    model: str = ""
    file_path: str = ""
    line_start: int | None = None
    line_end: int | None = None
    evidence: str = ""
    verification_status: str = "unverified"


class EvaluationCase(EvaluationModel):
    """一条可以本地运行，也可以上传到 LangSmith 的评测样例。"""

    case_id: str
    name: str
    diff: str
    expected_risk_level: RiskLevel | None = None
    reference_findings: list[ReferenceFinding] = Field(default_factory=list)
    # project_root 为空表示 diff-only 评测。公开 benchmark 初次接入时通常没有完整
    # checkout，因此会显式记录 context_mode=diff_only，避免把结果误解成全仓评测。
    project_root: str | None = None
    # 某些公开 benchmark 发布了冻结、版本化的上下文。使用它可与论文 baseline
    # 保持相同输入口径，也避免为了评测重复克隆数十个大型仓库。
    prebuilt_context: str | None = None
    prebuilt_context_name: str | None = None
    # True 表示 prebuilt_context 自身已经包含本 case 的 diff。该字段必须显式保存，
    # 不能靠字符串猜测，否则本地运行和 LangSmith 重跑可能得到不同 Prompt。
    prebuilt_context_owns_diff: bool = False
    review_profile: Literal["defect_detection", "human_review_alignment"] = "defect_detection"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationOutput(EvaluationModel):
    """CodeCross target 返回给本地 scorer/LangSmith 的统一输出。"""

    risk_level: RiskLevel | None = None
    # findings 保持向后兼容，并始终指向 benchmark 默认消费的 deduplicated 层。
    findings: list[PredictedFinding] = Field(default_factory=list)
    raw_findings: list[PredictedFinding] = Field(default_factory=list)
    deduplicated_findings: list[PredictedFinding] = Field(default_factory=list)
    verified_findings: list[PredictedFinding] = Field(default_factory=list)
    evaluation_mode: str = "evidence_verification"
    context_mode: str = "diff_only"
    project_profile: dict[str, Any] = Field(default_factory=dict)
    board_metrics: dict[str, Any] = Field(default_factory=dict)
    # 与质量分分离的运行观测和版本信息。单独字段可避免把 duration/model calls
    # 错当成 Board 的业务判断，也方便 LangSmith 按版本筛选实验。
    run_metadata: dict[str, Any] = Field(default_factory=dict)


class FindingMatch(EvaluationModel):
    """一次一对一 finding 匹配及其确定性得分。"""

    reference_index: int
    prediction_index: int
    score: float


class EvaluationScores(EvaluationModel):
    """单个 case 的指标；计数用于之后正确计算 micro average。"""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    risk_accuracy: float | None = None
    matches: list[FindingMatch] = Field(default_factory=list)
    passed: bool = False


class CaseEvaluationResult(EvaluationModel):
    """保留输出和分数，便于失败时直接定位具体 case。"""

    case_id: str
    name: str
    output: EvaluationOutput
    scores: EvaluationScores


class AggregateMetrics(EvaluationModel):
    """某一输出层在整个 suite 上的一组 micro/macro 指标。"""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    micro_precision: float = 0.0
    micro_recall: float = 0.0
    micro_f1: float = 0.0
    macro_precision: float = 0.0
    macro_recall: float = 0.0
    macro_f1: float = 0.0


class VerificationMetrics(EvaluationModel):
    """evidence_verification 的 suite 级状态分布和误杀指标。"""

    total_findings: int = 0
    supported_count: int = 0
    not_reproduced_count: int = 0
    not_testable_count: int = 0
    refuted_count: int = 0
    inconclusive_count: int = 0
    erroneous_refutation_count: int = 0
    support_rate: float = 0.0
    not_reproduced_rate: float = 0.0
    not_testable_rate: float = 0.0
    refutation_rate: float = 0.0
    inconclusive_rate: float = 0.0
    erroneous_refutation_rate: float = 0.0


class ConfidenceInterval(EvaluationModel):
    """bootstrap 百分位置信区间；estimate 保留原始完整样本点估计。"""

    estimate: float = 0.0
    lower: float = 0.0
    upper: float = 0.0
    confidence_level: float = 0.95
    resamples: int = 0


class SuiteReport(EvaluationModel):
    """本地评测集的聚合结果。"""

    case_count: int
    valid_case_count: int = 0
    invalid_case_count: int = 0
    valid_run_rate: float = 0.0
    micro_precision: float = 0.0
    micro_recall: float = 0.0
    micro_f1: float = 0.0
    macro_precision: float
    macro_recall: float
    macro_f1: float
    risk_accuracy: float | None
    failed_case_ids: list[str] = Field(default_factory=list)
    invalid_case_ids: list[str] = Field(default_factory=list)
    layer_metrics: dict[str, AggregateMetrics] = Field(default_factory=dict)
    per_language_metrics: dict[str, AggregateMetrics] = Field(default_factory=dict)
    language_valid_run_rate: dict[str, float] = Field(default_factory=dict)
    confidence_intervals: dict[str, ConfidenceInterval] = Field(default_factory=dict)
    verification_metrics: VerificationMetrics = Field(default_factory=VerificationMetrics)
    total_duration_ms: int = 0
    mean_duration_ms: float = 0.0
    max_duration_ms: int = 0
    model_call_count: int = 0
    model_call_failure_count: int = 0
    mean_model_calls_per_case: float = 0.0
    reviewer_call_count: int = 0
    reviewer_error_count: int = 0
    reviewer_error_rate: float = 0.0
    results: list[CaseEvaluationResult] = Field(default_factory=list)
