from __future__ import annotations

"""Review Board：CodeCross 的证据黑板。

Board 不调用模型，也不执行测试。它只做规则化的状态管理：
收集 reviewer 输出、初始化 verification、把 test result 转成 evidence、
更新 finding 状态，并告诉 workflow 下一轮该验证什么。
"""

import re
from collections import defaultdict

from .config import RunnerConfig
from .schemas import (
    BoardSummary,
    EvidenceItem,
    Finding,
    FindingCluster,
    FindingVerification,
    ReviewerResult,
    TargetedReview,
    TestCase,
    TestIntent,
    TestResult,
    TestResultAnalysis,
    TestRound,
    VerificationStatus,
)


SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

VERIFICATION_STATUS_LABELS = {
    "supported": "已被证据支持",
    "refuted": "已被测试反驳",
    "unverified": "尚未验证",
    "inconclusive": "证据不足",
    "not_testable": "无法自动测试",
    # Issue #2：模型交叉验证产生的 verification 状态。中文标签和 pytest 路径
    # 区分开，让 PR 评论一眼能看出“这条结论由谁背书”。
    "cross_supported": "模型交叉支持",
    "cross_refuted": "模型交叉反驳",
    "cross_inconclusive": "模型交叉证据不足",
    "needs_human_review": "需人工裁定",
}

STOP_REASON_LABELS = {
    "not stopped": "未提前停止",
    "verification_queue_empty": "验证队列已清空",
    "max_verification_rounds_reached": "达到最大验证轮次",
    "no_verification_progress": "本轮验证没有新增进展",
    "verification_queue_has_unresolved_targets": "仍有待验证目标",
}


def severity_rank(finding: Finding) -> int:
    """把严重等级转换成可排序数字。"""

    return SEVERITY_ORDER.get(finding.severity, 0)


def collect_review_outputs(
    reviewer_results: list[ReviewerResult],
) -> tuple[list[Finding], list[TestIntent]]:
    """把多个 reviewer 的输出拆成全局 findings 和 test intents。"""

    findings: list[Finding] = []
    intents: list[TestIntent] = []
    for result in reviewer_results:
        findings.extend(result.findings)
        intents.extend(result.test_intents)
    return findings, intents


def init_finding_verifications(findings: list[Finding]) -> dict[str, FindingVerification]:
    """为每条 Finding 初始化 Board 维护的验证状态。"""

    return {
        finding.finding_id: FindingVerification(
            finding_id=finding.finding_id,
            reason="Initial reviewer finding; evidence verification has not run yet.",
        )
        for finding in findings
    }


def init_static_evidence(findings: list[Finding]) -> dict[str, EvidenceItem]:
    """把 reviewer 原始 evidence 也登记为 weak static_evidence。"""

    evidence: dict[str, EvidenceItem] = {}
    for finding in findings:
        item = EvidenceItem(
            evidence_type="static_evidence",
            source=finding.source_reviewer,
            related_finding_ids=[finding.finding_id],
            summary=finding.evidence or finding.problem,
            raw_reference=f"{finding.file_path}:{finding.line_range.start or ''}",
            strength="weak",
            created_round=0,
        )
        evidence[item.evidence_id] = item
    return evidence


def _cluster_key(finding: Finding) -> str:
    """生成轻量聚类 key，用于 V1 的简单去重。"""

    words = re.findall(r"[a-zA-Z0-9_]+", finding.problem.lower())[:8]
    line = finding.line_range.start or 0
    return "|".join([finding.file_path, finding.category.lower(), str(line), " ".join(words)])


def cluster_findings(findings: list[Finding]) -> list[FindingCluster]:
    """按文件、类别、行号和问题关键词做轻量 finding cluster。"""

    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[_cluster_key(finding)].append(finding)

    clusters: list[FindingCluster] = []
    for items in grouped.values():
        top = max(items, key=severity_rank)
        clusters.append(
            FindingCluster(
                finding_ids=[item.finding_id for item in items],
                representative_problem=top.problem,
                reviewers=sorted({item.source_reviewer for item in items}),
                severity=top.severity,
            )
        )
    return clusters


def _finding_by_id(findings: list[Finding]) -> dict[str, Finding]:
    """把 findings 列表转成按 ID 查询的 dict。"""

    return {finding.finding_id: finding for finding in findings}


def build_verification_queue(
    findings: list[Finding],
    verifications: dict[str, FindingVerification],
    clusters: list[FindingCluster],
) -> list[str]:
    """根据 Board 状态生成下一轮验证队列。

    V1 优先验证 high/critical，其次验证多个 reviewer 命中的 medium，
    已经 supported/refuted/not_testable 的 finding 不会再次入队。
    """

    cluster_sizes: dict[str, int] = {}
    for cluster in clusters:
        for finding_id in cluster.finding_ids:
            cluster_sizes[finding_id] = len(cluster.reviewers)

    candidates: list[Finding] = []
    for finding in findings:
        verification = verifications.get(finding.finding_id)
        if verification is None:
            continue
        if verification.status not in {"unverified", "inconclusive"}:
            continue
        if finding.severity in {"critical", "high"}:
            candidates.append(finding)
        elif finding.severity == "medium" and cluster_sizes.get(finding.finding_id, 1) > 1:
            candidates.append(finding)
        elif finding.related_test_intent_ids:
            candidates.append(finding)

    candidates.sort(
        key=lambda item: (
            -SEVERITY_ORDER.get(item.severity, 0),
            item.file_path,
            item.finding_id,
        )
    )
    return [finding.finding_id for finding in candidates]


def build_board_summary(
    *,
    reviewer_results: list[ReviewerResult],
    findings: list[Finding],
    test_intents: list[TestIntent],
    test_cases: list[TestCase],
    test_results: list[TestResult],
    test_rounds: list[TestRound],
    verifications: dict[str, FindingVerification],
    evidence_map: dict[str, EvidenceItem],
    coverage_gaps: list[str],
    stop_reason: str,
) -> BoardSummary:
    """构造 Board 的汇总视图，供 evaluator/report/workflow 使用。"""

    clusters = cluster_findings(findings)
    queue = build_verification_queue(findings, verifications, clusters)
    return BoardSummary(
        reviewer_count=len(reviewer_results),
        finding_count=len(findings),
        test_intent_count=len(test_intents),
        test_case_count=len(test_cases),
        test_round_count=len(test_rounds),
        passed_count=sum(1 for result in test_results if result.status == "passed"),
        failed_count=sum(1 for result in test_results if result.status == "failed"),
        error_count=sum(1 for result in test_results if result.status in {"error", "timeout", "unsafe"}),
        skipped_count=sum(1 for result in test_results if result.status in {"skipped", "duplicate"}),
        finding_clusters=clusters,
        finding_verifications=verifications,
        verification_queue=queue,
        failed_tests=[result for result in test_results if result.status in {"failed", "error", "timeout"}],
        coverage_gaps=coverage_gaps,
        evidence_map=evidence_map,
        stop_reason=stop_reason,
    )


def select_next_verification_targets(
    findings: list[Finding],
    board_summary: BoardSummary,
    runner_config: RunnerConfig,
) -> list[Finding]:
    """从 verification_queue 中挑选本轮要处理的 Finding。"""

    finding_map = _finding_by_id(findings)
    selected: list[Finding] = []
    for finding_id in board_summary.verification_queue:
        finding = finding_map.get(finding_id)
        if finding is None:
            continue
        selected.append(finding)
        if len(selected) >= runner_config.max_tests_per_round:
            break
    return selected


def _result_to_evidence(result: TestResult) -> EvidenceItem:
    """把 executor 的 TestResult 映射成 Board EvidenceItem。"""

    # Issue #2：LLM 交叉验证结果走专门的 evidence_type，
    # 这样 reporting / evaluator 可以用 evidence_type 直接区分两条证据线。
    if result.evidence_kind == "llm_cross_verification":
        payload = result.cross_payload
        # status -> 证据强度。这里再经手一次，保持 Board 内部不依赖
        # CrossVerificationPayload.suggested_strength（万一未来 ReproAgent
        # 给的 hint 我们不想信任，可以在这里覆写规则）。
        if result.status == "cross_confirmed":
            strength = "medium"
        elif result.status == "cross_refuted":
            strength = "medium"
        elif result.status == "cross_supported":
            strength = "weak"
        else:
            strength = "weak"
        summary = (
            payload.reasoning
            if payload and payload.reasoning
            else f"Cross verification status: {result.status}"
        )
        raw_reference = ""
        if payload:
            ref_parts: list[str] = []
            if payload.reproduction_steps:
                ref_parts.append("steps:\n" + "\n".join(payload.reproduction_steps))
            if payload.counter_examples:
                ref_parts.append("counter_examples:\n" + "\n".join(payload.counter_examples))
            if payload.expected_observation:
                ref_parts.append(f"expected: {payload.expected_observation}")
            raw_reference = "\n\n".join(ref_parts)[-2000:]
        return EvidenceItem(
            evidence_type="llm_cross_verification",
            source=payload.model if payload else "llm_cross_verification",
            related_finding_ids=result.related_finding_ids,
            related_test_case_id=result.test_case_id,
            related_test_result_id=result.test_result_id,
            summary=summary,
            raw_reference=raw_reference,
            strength=strength,
            created_round=result.round_index,
        )

    if result.status == "failed" and result.is_reproducible:
        evidence_type = "test_failure"
        strength = "strong"
    elif result.status == "failed":
        # 非可复现失败不能作为“finding 成立”的强证据。
        # 它仍然会被记录下来，方便人工排查，但 Board 只把它当 executor_error。
        evidence_type = "executor_error"
        strength = "weak"
    elif result.status == "passed":
        evidence_type = "test_pass"
        strength = "medium"
    elif result.status == "unsafe":
        evidence_type = "coverage_gap"
        strength = "medium"
    else:
        evidence_type = "executor_error"
        strength = "weak"

    return EvidenceItem(
        evidence_type=evidence_type,
        source="deterministic_executor",
        related_finding_ids=result.related_finding_ids,
        related_test_case_id=result.test_case_id,
        related_test_result_id=result.test_result_id,
        summary=result.failure_summary or f"Test result status: {result.status}",
        raw_reference=(result.stdout + "\n" + result.stderr).strip()[-2000:],
        strength=strength,
        created_round=result.round_index,
    )


def _apply_cross_decision(
    *,
    verification: FindingVerification,
    counts: dict[str, int],
    previous_status: str,
    round_index: int,
) -> None:
    """根据本 finding 上累计的 cross verification 计数更新 verification。

    决策规则（保守优先，源自 Issue #2 设计稿）：
    - confirmed 与 refuted 同时存在 -> needs_human_review。模型互相矛盾，
      不能让 board 选边站，必须升级到人。
    - 仅 refuted -> cross_refuted。
    - 仅 confirmed -> cross_supported（只到 “supported”，不能直接到原 supported；
      因为这条证据来自模型推理，不是真实 pytest 失败）。
    - 仅 supported -> cross_inconclusive 升级到 cross_supported？不。
      模型自己只敢说 supported（弱信心）的情况下，board 同样保守落到
      cross_inconclusive，避免 evaluator 误把它当成结论。
    - 其它（仅 inconclusive 或全空）-> cross_inconclusive。

    pytest 已经给出 supported/refuted/not_testable 的 finding 不会被覆盖；
    这层保护在调用点之前就该过滤，但这里再校一道更保险。
    """

    if previous_status in {"supported", "refuted", "not_testable"}:
        return

    confirmed = counts.get("confirmed", 0)
    refuted = counts.get("refuted", 0)
    supported = counts.get("supported", 0)

    if confirmed and refuted:
        verification.status = "needs_human_review"
        verification.confidence = "low"
        verification.reason = "Cross verification 出现冲突（同一 finding 同时被 confirmed 与 refuted）。"
        verification.next_action = "请人工裁定后再决定该 finding 是否成立。"
    elif refuted:
        verification.status = "cross_refuted"
        verification.confidence = "medium"
        verification.reason = "Cross verification 给出反例，倾向于反驳该 finding。"
        verification.next_action = "可关闭该 finding，但建议人工再核对反例细节。"
    elif confirmed:
        verification.status = "cross_supported"
        verification.confidence = "medium"
        verification.reason = "Cross verification 给出可复现步骤，倾向于支持该 finding。"
        verification.next_action = "请人工执行复现步骤后再合入修复。"
    elif supported:
        verification.status = "cross_inconclusive"
        verification.confidence = "low"
        verification.reason = "Cross verification 仅给出弱支持，证据不足以下结论。"
        verification.next_action = "建议补充上下文或人工复核。"
    else:
        verification.status = "cross_inconclusive"
        verification.confidence = "low"
        verification.reason = "Cross verification 未给出有效结论（abstain / inconclusive）。"
        verification.next_action = "请人工复核或扩大代码上下文后重试。"

    verification.last_updated_round = max(verification.last_updated_round, round_index)


def update_finding_verifications_from_results(
    verifications: dict[str, FindingVerification],
    evidence_map: dict[str, EvidenceItem],
    test_results: list[TestResult],
) -> tuple[dict[str, FindingVerification], dict[str, EvidenceItem], dict[str, str]]:
    """根据测试结果更新 finding verification。

    这是 V1.1 的核心证据规则：
    - 相关测试 failed 且可复现 => finding supported
    - 相关测试 passed          => finding refuted，但只给 medium confidence
    - flaky/error/timeout      => inconclusive，不能支持也不能反驳
    - unsafe                  => not_testable，需要人工处理
    """

    status_changes: dict[str, str] = {}

    # Issue #2：把 cross verification 结果按 finding 聚合一次。Board 状态机的
    # 重要设计决策——cross 路径的状态汇总要 “集体决议”：同一 finding 上
    # confirmed + refuted 同时出现要落到 needs_human_review，而不是按出现顺序
    # 被最后一个 result 覆盖。
    cross_by_finding: dict[str, dict[str, int]] = {}
    cross_round_by_finding: dict[str, int] = {}
    for result in test_results:
        if result.evidence_kind != "llm_cross_verification":
            continue
        for finding_id in result.related_finding_ids:
            counts = cross_by_finding.setdefault(
                finding_id, {"confirmed": 0, "refuted": 0, "supported": 0, "inconclusive": 0}
            )
            if result.status == "cross_confirmed":
                counts["confirmed"] += 1
            elif result.status == "cross_refuted":
                counts["refuted"] += 1
            elif result.status == "cross_supported":
                counts["supported"] += 1
            else:
                counts["inconclusive"] += 1
            cross_round_by_finding[finding_id] = max(
                cross_round_by_finding.get(finding_id, 0), result.round_index
            )

    for result in test_results:
        if not result.related_finding_ids:
            continue
        evidence = _result_to_evidence(result)
        evidence_map[evidence.evidence_id] = evidence

        for finding_id in result.related_finding_ids:
            verification = verifications.get(finding_id)
            if verification is None:
                continue
            if evidence.evidence_id not in verification.evidence_ids:
                verification.evidence_ids.append(evidence.evidence_id)
            if result.test_result_id not in verification.related_test_result_ids:
                verification.related_test_result_ids.append(result.test_result_id)
            verification.last_updated_round = result.round_index

            previous = verification.status

            if result.evidence_kind == "llm_cross_verification":
                # cross 路径的状态决议在循环外做的聚合上读取，循环这里只负责
                # 把单个 finding 的 cross 决议落地一次（多次落地结果一致，
                # 因为聚合表是同一份）。
                counts = cross_by_finding.get(finding_id, {})
                _apply_cross_decision(
                    verification=verification,
                    counts=counts,
                    previous_status=previous,
                    round_index=cross_round_by_finding.get(finding_id, result.round_index),
                )
                if previous != verification.status:
                    status_changes[finding_id] = f"{previous} -> {verification.status}"
                continue

            if result.status == "failed" and result.is_reproducible:
                verification.status = "supported"
                verification.confidence = "high"
                verification.reason = (
                    "A generated pytest case failed twice and reproduced evidence for this finding."
                )
                verification.next_action = "Review and fix the finding."
            elif result.status == "failed" and verification.status not in {"supported", "refuted", "not_testable"}:
                verification.status = "inconclusive"
                verification.confidence = "low"
                verification.reason = (
                    "The generated pytest case failed but was not marked reproducible."
                )
                verification.next_action = "Rerun or generate a narrower test before trusting this evidence."
            elif result.status == "passed" and verification.status != "supported":
                verification.status = "refuted"
                verification.confidence = "medium"
                verification.reason = (
                    "The generated pytest case passed, so this suspected issue was not reproduced."
                )
                verification.next_action = "No further automated verification is needed."
            elif result.status == "unsafe" and verification.status not in {"supported", "refuted"}:
                verification.status = "not_testable"
                verification.confidence = "medium"
                verification.reason = "The generated test was blocked by the sanitizer."
                verification.next_action = "Manual review is required."
            elif verification.status not in {"supported", "refuted", "not_testable"}:
                verification.status = "inconclusive"
                verification.confidence = "low"
                verification.reason = (
                    "The executor could not produce decisive evidence for this finding."
                )
                verification.next_action = "Generate a narrower test or run targeted review."

            if previous != verification.status:
                status_changes[finding_id] = f"{previous} -> {verification.status}"

    return verifications, evidence_map, status_changes


def apply_analysis_updates(
    verifications: dict[str, FindingVerification],
    analysis: TestResultAnalysis,
) -> dict[str, str]:
    """应用 Test Result Analyzer 的状态建议。

    已经 supported/refuted 的结论更强，不被 analyzer 的软建议覆盖。
    """

    changes: dict[str, str] = {}
    for finding_id, suggested_status in analysis.suggested_status_updates.items():
        verification = verifications.get(finding_id)
        if verification is None:
            continue
        if verification.status in {"supported", "refuted"}:
            continue
        if verification.status == "not_testable":
            # sanitizer 拦截代表“这个测试不能跑”，优先级高于 analyzer 的软建议。
            # 否则 unsafe 测试可能被 analyzer 重新改成 inconclusive，丢失人工处理信号。
            continue
        previous = verification.status
        verification.status = suggested_status
        verification.last_updated_round = analysis.round_index
        verification.reason = "Updated by test result analyzer."
        if previous != suggested_status:
            changes[finding_id] = f"{previous} -> {suggested_status}"
    return changes


def apply_targeted_review(
    verifications: dict[str, FindingVerification],
    evidence_map: dict[str, EvidenceItem],
    targeted_review: TargetedReview,
) -> tuple[dict[str, FindingVerification], dict[str, EvidenceItem], dict[str, str]]:
    """把 Targeted Reviewer 的复审结果登记为 evidence 并更新状态。"""

    evidence = EvidenceItem(
        evidence_type="targeted_review_note",
        source=targeted_review.reviewer_name,
        related_finding_ids=targeted_review.target_finding_ids,
        related_targeted_review_id=targeted_review.targeted_review_id,
        summary=targeted_review.analysis,
        strength="medium",
        created_round=targeted_review.round_index,
    )
    evidence_map[evidence.evidence_id] = evidence
    changes: dict[str, str] = {}

    for finding_id, status in targeted_review.suggested_status_updates.items():
        verification = verifications.get(finding_id)
        if verification is None:
            continue
        if evidence.evidence_id not in verification.evidence_ids:
            verification.evidence_ids.append(evidence.evidence_id)
        if targeted_review.targeted_review_id not in verification.related_targeted_review_ids:
            verification.related_targeted_review_ids.append(targeted_review.targeted_review_id)
        previous = verification.status
        if previous not in {"supported", "refuted"}:
            verification.status = status
        verification.last_updated_round = targeted_review.round_index
        verification.reason = f"Targeted reviewer update: {targeted_review.analysis[:240]}"
        if previous != verification.status:
            changes[finding_id] = f"{previous} -> {verification.status}"

    return verifications, evidence_map, changes


def should_continue_verification(
    board_summary: BoardSummary,
    round_index: int,
    runner_config: RunnerConfig,
    status_changes: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """由 Board 状态决定 Evidence Verification Loop 是否继续。"""

    if not board_summary.verification_queue:
        return False, "verification_queue_empty"
    if round_index >= runner_config.max_verification_rounds:
        return False, "max_verification_rounds_reached"
    if status_changes is not None and not status_changes:
        # 无进展早停：如果本轮没有任何 verification 状态变化，继续跑同一批目标
        # 只会浪费 token/时间，还可能让用户误以为系统在产生新证据。
        return False, "no_verification_progress"
    return True, "verification_queue_has_unresolved_targets"


def render_board_markdown(summary: BoardSummary) -> str:
    """把 BoardSummary 渲染成最终报告中的 Markdown 段落。"""

    status_counts: dict[str, int] = defaultdict(int)
    for verification in summary.finding_verifications.values():
        status_counts[verification.status] += 1
    status_text = ", ".join(
        f"{VERIFICATION_STATUS_LABELS.get(key, key)}：{value}"
        for key, value in sorted(status_counts.items())
    )
    stop_reason = STOP_REASON_LABELS.get(
        summary.stop_reason or "not stopped",
        summary.stop_reason or "未提前停止",
    )
    return (
        "## 证据看板\n\n"
        f"- 审查器数量：{summary.reviewer_count}\n"
        f"- 问题数量：{summary.finding_count}\n"
        f"- 测试意图：{summary.test_intent_count}\n"
        f"- 测试用例：{summary.test_case_count}\n"
        f"- 验证轮次：{summary.test_round_count}\n"
        f"- 通过测试：{summary.passed_count}\n"
        f"- 失败测试：{summary.failed_count}\n"
        f"- 错误/阻塞测试：{summary.error_count}\n"
        f"- 验证状态：{status_text or '无'}\n"
        f"- 停止原因：{stop_reason}\n"
    )
