from __future__ import annotations

"""SWE-PRBench v0.4.1 的三分类 Judge 与核心指标。

官方协议与普通二元 matching 最大的不同是：没有命中 human golden 的候选不一定是
假阳性。只要候选与已展示代码一致，它就是 ``PLAUSIBLE``；只有事实错误或捏造代码
才是 ``FABRICATED``。本模块复现 Detection Rate、FPR、P/R/F1、actionability 等
不依赖 embedding 的核心指标。官方 Overall 还需要 code embedding semantic alignment，
所以这里故意不伪造 Overall；调用方必须看到 ``overall_score=None``。
"""

from dataclasses import replace
import re
from typing import Literal

from pydantic import Field

from ..llm import call_model_json, json_schema_instruction
from ..schemas import CodeCrossModel
from .judges import build_judge_settings
from .models import PredictedFinding, ReferenceFinding


SWE_PRBENCH_JUDGE_PROMPT_VERSION = "official-v1.0-codecross-core"


class SWEAgentClassification(CodeCrossModel):
    """对一条 CodeCross finding 的官方三分类裁决。"""

    comment_id: str
    classification: Literal["CONFIRMED", "PLAUSIBLE", "FABRICATED"]
    matched_human_comment_id: str | None = None
    actionability_score: int = Field(default=3, ge=1, le=5)
    reasoning: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SWEHumanCommentStatus(CodeCrossModel):
    """一条 human golden 是否被某条 CONFIRMED finding 覆盖。"""

    comment_id: str
    status: Literal["CAUGHT", "MISSED"]
    matched_agent_comment_id: str | None = None


class SWEJudgeBatch(CodeCrossModel):
    agent_classifications: list[SWEAgentClassification] = Field(default_factory=list)
    human_comment_statuses: list[SWEHumanCommentStatus] = Field(default_factory=list)


class SWEPRBenchCoreMetrics(CodeCrossModel):
    """无需 embedding 即可严格复现的官方核心指标。"""

    total_agent_comments: int
    total_human_comments: int
    confirmed_count: int
    plausible_count: int
    fabricated_count: int
    caught_human_comments: int
    missed_human_comments: int
    detection_rate: float
    false_positive_rate: float
    precision: float
    recall: float
    f1_score: float
    actionability_score: float
    efficiency: float
    plausible_penalty: float
    redundancy_penalty: float = 0.0
    overall_score: float | None = None
    compatibility_status: Literal["official_core_without_embedding"] = (
        "official_core_without_embedding"
    )
    matched_pairs: list[dict[str, str | int | float]] = Field(default_factory=list)
    classifications: list[SWEAgentClassification] = Field(default_factory=list)


_SYSTEM_PROMPT = """You are an expert evaluator assessing AI code review quality against human expert review.

You are given:
1) PR diff
2) Human review comments (ground truth)
3) AI agent review comments

## Step 1: Classify each agent comment

Use these definitions:

- CONFIRMED: The agent comment addresses the same underlying issue as a human
  comment, even if phrased differently, at a different abstraction level, or
  without citing the exact line number. Partial matches count if the core
  concern is the same. Set matched_human_comment_id to the matching human comment.

- PLAUSIBLE: A reasonable observation about the code that has no factual errors,
  but does not match any human comment. The agent may be correct but the human
  reviewers did not raise this point.

- FABRICATED: The comment contains factual errors about what the code does,
  references code that does not exist in the diff, or misidentifies the behavior
  of the code. Not being in ground truth is NOT sufficient for FABRICATED.

Examples of CONFIRMED (different wording, same issue):
  Human: "This needs to handle info < 0 per LAPACK docs"
  Agent:  "Error handling only checks positive return values but LAPACK
           can return negative codes for invalid arguments"
  -> CONFIRMED

  Human: "free(NULL) is a no-op, remove the null check"
  Agent:  "The null check before free() is unnecessary"
  -> CONFIRMED

## Step 2: Classify each human comment

Use your Step 1 results -- do not classify independently:
- If a human comment H was matched by a CONFIRMED agent comment X,
  then H is CAUGHT with matched_agent_comment_id = X
- Otherwise H is MISSED

Return one classification/status for every supplied id. Each agent and human
comment may participate in at most one match."""


def _render_judge_input(
    diff: str,
    references: list[ReferenceFinding],
    predictions: list[PredictedFinding],
) -> str:
    """使用稳定 ID 拼装 Judge 输入；golden 只在 evaluator 内出现。"""

    human = "\n\n".join(
        f"human-{index}\nfile={item.file_path or 'general'} line={item.line_start}\n{item.description}"
        for index, item in enumerate(references)
    ) or "(none)"
    agent = "\n\n".join(
        f"agent-{index}\nfile={item.file_path or 'general'} line={item.line_start}\n"
        f"body={item.description}\nevidence={item.evidence}"
        for index, item in enumerate(predictions)
    ) or "(none)"
    structured_diff = _build_structured_diff_snippet(diff)
    return f"PR DIFF:\n{structured_diff}\n\nHUMAN COMMENTS:\n{human}\n\nAI AGENT COMMENTS:\n{agent}"


def _build_structured_diff_snippet(diff: str, max_chars: int = 12000) -> str:
    """复现官方 Judge 的 diff chunk 排序，避免简单截断只看到 PR 前半段。

    实现文件优先于测试文件；同类文件中，变更行和逻辑关键字更多的 hunk 优先。
    方法源自 FoundryHQ-AI/swe-prbench v0.4.1（MIT），保留相同 12k 字符预算。
    """

    chunks = _parse_diff_chunks(diff)
    chunks.sort(
        key=lambda chunk: (
            chunk["is_test"],
            -(chunk["lines_changed"] + chunk["logic_markers"] * 3),
        )
    )
    selected: list[str] = []
    used = 0
    for chunk in chunks:
        raw = str(chunk["raw"])
        projected = used + len(raw) + (2 if selected else 0)
        if not raw or projected > max_chars:
            continue
        selected.append(raw)
        used = projected
    return "\n\n".join(selected) if selected else diff[:max_chars]


def _parse_diff_chunks(diff: str) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    current_file = ""
    current_lines: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current_lines:
                chunks.append(_build_diff_chunk(current_file, "\n".join(current_lines)))
            match = re.search(r" b/(.+)$", line)
            current_file = match.group(1) if match else ""
            current_lines = [line]
        elif current_lines:
            current_lines.append(line)
    if current_lines:
        chunks.append(_build_diff_chunk(current_file, "\n".join(current_lines)))
    return chunks


def _build_diff_chunk(file_path: str, raw: str) -> dict[str, object]:
    changed = sum(
        1
        for line in raw.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )
    lowered = raw.lower()
    markers = sum(
        marker in lowered
        for marker in (
            "if ", "switch ", "case ", "return ", "error", "panic", "parse", "timeout", "duration"
        )
    )
    path = file_path.lower()
    is_test = any(
        token in path
        for token in ("test/", "tests/", "_test.", ".test.", ".spec.", "__tests__/")
    )
    return {
        "file_path": file_path,
        "raw": raw,
        "lines_changed": changed,
        "logic_markers": markers,
        "is_test": is_test,
    }


def run_swe_prbench_judge(
    *,
    diff: str,
    references: list[ReferenceFinding],
    predictions: list[PredictedFinding],
) -> SWEJudgeBatch:
    """一次结构化调用分类整条 PR，避免逐 pair 的 O(golden×candidate) 成本。"""

    if not predictions:
        return SWEJudgeBatch(
            human_comment_statuses=[
                SWEHumanCommentStatus(comment_id=f"human-{index}", status="MISSED")
                for index in range(len(references))
            ]
        )
    settings = replace(
        build_judge_settings(),
        max_tokens=4000,
        timeout_seconds=180,
        retry_count=2,
    )
    result = call_model_json(
        settings,
        [
            ("system", _SYSTEM_PROMPT),
            (
                "user",
                f"{_render_judge_input(diff, references, predictions)}\n\n"
                f"{json_schema_instruction(SWEJudgeBatch)}",
            ),
        ],
        SWEJudgeBatch,
    )
    return result  # type: ignore[return-value]


def score_swe_prbench_core(
    references: list[ReferenceFinding],
    predictions: list[PredictedFinding],
    decision: SWEJudgeBatch,
) -> SWEPRBenchCoreMetrics:
    """按官方核心规则归一化 Judge 输出并计算 DR/FPR。

    模型可能重复把多条 candidate 指向同一 human comment。这里按 actionability、
    confidence 从高到低做一对一选择；未入选但非 FABRICATED 的 candidate 归为
    PLAUSIBLE，防止重复 finding 虚增 detection。
    """

    valid_agent_ids = {f"agent-{index}" for index in range(len(predictions))}
    valid_human_ids = {f"human-{index}" for index in range(len(references))}
    supplied = {item.comment_id: item for item in decision.agent_classifications}
    candidates = [
        item
        for item in supplied.values()
        if item.comment_id in valid_agent_ids
        and item.classification == "CONFIRMED"
        and item.matched_human_comment_id in valid_human_ids
    ]
    candidates.sort(key=lambda item: (item.actionability_score, item.confidence), reverse=True)

    used_agents: set[str] = set()
    used_humans: set[str] = set()
    matched_pairs: list[dict[str, str | int | float]] = []
    for item in candidates:
        human_id = str(item.matched_human_comment_id)
        if item.comment_id in used_agents or human_id in used_humans:
            continue
        used_agents.add(item.comment_id)
        used_humans.add(human_id)
        matched_pairs.append(
            {
                "agent_comment_id": item.comment_id,
                "human_comment_id": human_id,
                "actionability": item.actionability_score,
                "confidence": item.confidence,
            }
        )

    normalized: list[SWEAgentClassification] = []
    for index in range(len(predictions)):
        agent_id = f"agent-{index}"
        raw = supplied.get(agent_id)
        if agent_id in used_agents and raw is not None:
            classification = "CONFIRMED"
            matched_human = raw.matched_human_comment_id
        elif raw is not None and raw.classification == "FABRICATED":
            classification = "FABRICATED"
            matched_human = None
        else:
            classification = "PLAUSIBLE"
            matched_human = None
        normalized.append(
            SWEAgentClassification(
                comment_id=agent_id,
                classification=classification,
                matched_human_comment_id=matched_human,
                actionability_score=raw.actionability_score if raw else 3,
                reasoning=raw.reasoning if raw else "Judge omitted this id; conservative PLAUSIBLE fallback.",
                confidence=raw.confidence if raw else 0.0,
            )
        )

    total_agent = len(predictions)
    total_human = len(references)
    confirmed = len(used_agents)
    plausible = sum(item.classification == "PLAUSIBLE" for item in normalized)
    fabricated = sum(item.classification == "FABRICATED" for item in normalized)
    caught = len(used_humans)
    precision = confirmed / total_agent if total_agent else 0.0
    recall = caught / total_human if total_human else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    actionability = (
        (sum(item.actionability_score for item in normalized) / total_agent - 1.0) / 4.0
        if total_agent
        else 0.0
    )
    plausible_penalty = (
        max(0.0, plausible / total_agent - 0.7) if total_agent >= 3 else 0.0
    )
    return SWEPRBenchCoreMetrics(
        total_agent_comments=total_agent,
        total_human_comments=total_human,
        confirmed_count=confirmed,
        plausible_count=plausible,
        fabricated_count=fabricated,
        caught_human_comments=caught,
        missed_human_comments=max(0, total_human - caught),
        detection_rate=round(recall, 6),
        false_positive_rate=round(fabricated / total_agent if total_agent else 0.0, 6),
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1_score=round(f1, 6),
        actionability_score=round(actionability, 6),
        efficiency=round(precision, 6),
        plausible_penalty=round(plausible_penalty, 6),
        matched_pairs=matched_pairs,
        classifications=normalized,
    )
