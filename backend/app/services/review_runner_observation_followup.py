from __future__ import annotations

from typing import Callable

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject


def normalize_confidence_value(value: object, fallback: float = 0.0) -> float:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"high", "高", "高置信", "高置信度"}:
            return 0.86
        if normalized in {"medium", "mid", "中", "中等", "中置信", "中置信度"}:
            return 0.68
        if normalized in {"low", "低", "低置信", "低置信度"}:
            return 0.38
    try:
        parsed = float(value)
    except Exception:
        parsed = fallback
    return min(0.99, max(0.0, parsed))


def collect_batch_review_observations(
    repository_context: dict[str, object],
    batch_items: list[dict[str, object]],
    *,
    normalize_review_observations: Callable[[object], list[dict[str, object]]],
) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in normalize_review_observations(repository_context.get("review_observations")):
        observation_id = str(item.get("observation_id") or "").strip()
        if observation_id and observation_id in seen:
            continue
        if observation_id:
            seen.add(observation_id)
        collected.append(item)
    for batch_item in batch_items:
        batch_context = batch_item.get("repository_context")
        if not isinstance(batch_context, dict):
            continue
        for item in normalize_review_observations(batch_context.get("review_observations")):
            observation_id = str(item.get("observation_id") or "").strip()
            if observation_id and observation_id in seen:
                continue
            if observation_id:
                seen.add(observation_id)
            collected.append(item)
    return collected


def find_uncovered_review_observations(
    candidates: list[dict[str, object]],
    observations: list[dict[str, object]],
    *,
    normalize_text_list: Callable[[object, list[str]], list[str]],
    normalize_optional_line_value: Callable[[object], int | None],
) -> list[dict[str, object]]:
    if not observations:
        return []
    covered_ids: set[str] = set()
    covered_lines: dict[str, set[int]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for observation_id in normalize_text_list(candidate.get("observation_ids"), []):
            covered_ids.add(observation_id)
        candidate_file_path = str(candidate.get("file_path") or "").strip()
        candidate_line = normalize_optional_line_value(candidate.get("line_start"))
        if candidate_file_path and candidate_line is not None:
            covered_lines.setdefault(candidate_file_path, set()).add(int(candidate_line))

    uncovered: list[dict[str, object]] = []
    for item in observations:
        observation_id = str(item.get("observation_id") or "").strip()
        if observation_id and observation_id in covered_ids:
            continue
        observation_file_path = str(item.get("file_path") or "").strip()
        observation_line = normalize_optional_line_value(item.get("line_start"))
        if observation_file_path and observation_line is not None:
            if int(observation_line) in covered_lines.get(observation_file_path, set()):
                continue
        uncovered.append(dict(item))
    return uncovered[:8]


def candidate_strength_score(
    candidate: dict[str, object],
    *,
    normalize_text_list: Callable[[object, list[str]], list[str]],
) -> float:
    finding_type = str(candidate.get("finding_type") or "").strip().lower()
    severity = str(candidate.get("severity") or "").strip().lower()
    observation_count = len(normalize_text_list(candidate.get("observation_ids"), []))
    confidence = normalize_confidence_value(candidate.get("confidence"), 0.0)
    score = confidence
    if finding_type == "direct_defect":
        score += 1.0
    elif finding_type == "test_gap":
        score += 0.5
    if severity in {"blocker", "critical", "high"}:
        score += 0.6
    elif severity == "medium":
        score += 0.2
    if observation_count:
        score += 0.3
    if bool(candidate.get("direct_evidence")):
        score += 0.3
    return score


def merge_expert_analysis_candidates(
    base_candidates: list[dict[str, object]],
    extra_candidates: list[dict[str, object]],
    *,
    max_findings: int,
    normalize_optional_line_value: Callable[[object], int | None],
    normalize_text_list: Callable[[object, list[str]], list[str]],
) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, str]] = set()
    signal_titles = {"循环调用放大", "承诺未落地"}
    signal_best_by_anchor: dict[tuple[str, str, int], dict[str, object]] = {}
    for item in list(base_candidates) + list(extra_candidates):
        if not isinstance(item, dict):
            continue
        file_key = str(item.get("file_path") or "").strip().lower()
        title_key = str(item.get("title") or "").strip()
        line_key = int(normalize_optional_line_value(item.get("line_start")) or 0)
        if title_key in signal_titles:
            signal_anchor = (file_key, title_key.lower(), line_key)
            incumbent = signal_best_by_anchor.get(signal_anchor)
            if incumbent is None or candidate_strength_score(
                item,
                normalize_text_list=normalize_text_list,
            ) > candidate_strength_score(incumbent, normalize_text_list=normalize_text_list):
                signal_best_by_anchor[signal_anchor] = dict(item)
            continue
        key = (
            file_key,
            title_key.lower(),
            line_key,
            str(item.get("claim") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(item))
    merged.extend(signal_best_by_anchor.values())
    merged.sort(
        key=lambda item: (
            str(item.get("file_path") or "").strip().lower(),
            int(normalize_optional_line_value(item.get("line_start")) or 0),
            -candidate_strength_score(item, normalize_text_list=normalize_text_list),
        )
    )
    return merged[: max(1, int(max_findings or 1))]


def build_observation_followup_prompt(
    *,
    subject: ReviewSubject,
    expert: ExpertProfile,
    uncovered_observations: list[dict[str, object]],
    existing_candidates: list[dict[str, object]],
    multi_file_prompt_appendix: str,
    repository_source_blocks: str,
    max_findings: int,
    normalize_optional_line_value: Callable[[object], int | None],
) -> str:
    lines = [
        f"你是 {expert.name_zh}，现在只做遗漏问题增量复核。",
        "要求：",
        "1. 下面给出的 observation 是首轮结果尚未明确覆盖的可疑代码现象；",
        "2. 你必须逐条判断 observation 是否构成真实问题；",
        "3. 只有确认成立且不是首轮已输出重复问题时，才输出新的 candidate_finding；",
        "4. 每条新增 candidate_finding 必须带 rule_id、file_path、line、title、evidence、confidence、observation_ids；",
        "5. 如果没有新增问题，candidate_findings 返回空数组，但仍要输出 rule_check_results、context_requests、self_check。",
        f"6. 最多新增 {max(1, int(max_findings or 1))} 条 candidate_findings。",
        "",
        f"仓库: {subject.repo_id}",
        f"目标分支: {subject.target_ref}",
        f"变更文件: {', '.join(subject.changed_files[:20]) or 'unknown'}",
        "",
        "首轮已输出 findings 摘要：",
    ]
    if existing_candidates:
        for item in existing_candidates[:12]:
            lines.append(
                f"- {str(item.get('file_path') or '').strip() or 'unknown'}"
                f":L{int(normalize_optional_line_value(item.get('line_start')) or 1)} "
                f"{str(item.get('title') or '').strip() or 'untitled'} | "
                f"{str(item.get('claim') or '').strip() or 'no-claim'}"
            )
    else:
        lines.append("- 首轮没有输出 findings。")
    lines.extend(["", "待复核 observation："])
    for item in uncovered_observations:
        observation_id = str(item.get("observation_id") or "").strip() or "observation"
        file_path = str(item.get("file_path") or "").strip() or "unknown"
        line_start = int(normalize_optional_line_value(item.get("line_start")) or 1)
        summary = str(item.get("summary") or "").strip()
        kind = str(item.get("kind") or "").strip()
        lines.append(f"- {observation_id} | {file_path}:L{line_start} | {kind} | {summary}")
        evidence = [str(value).strip() for value in list(item.get("evidence") or []) if str(value).strip()]
        if evidence:
            lines.append(f"  证据: {' / '.join(evidence[:3])}")
        risk_hints = [str(value).strip() for value in list(item.get("risk_hints") or []) if str(value).strip()]
        if risk_hints:
            lines.append(f"  风险提示: {' / '.join(risk_hints[:3])}")

    if multi_file_prompt_appendix.strip():
        lines.extend(["", multi_file_prompt_appendix])
    if repository_source_blocks.strip():
        lines.extend(["", "关键源码上下文：", repository_source_blocks])

    lines.extend(
        [
            "",
            "输出格式要求：",
            '仅输出 JSON：{"rule_check_results":[],"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,"used_context_files":[],"unverified_assumptions":[]}}',
            "rule_check_results 每条至少包含: rule_id, status, evidence, missing_context, reason；rule_id 可使用 observation_id 或真实命中规则 ID。",
            "candidate_findings 每条至少包含: rule_id, file_path, line, title, evidence, confidence, observation_ids；line 必须落在真实变更行或 observation 行。",
        ]
    )
    return "\n".join(lines)


def build_forced_observation_candidates(
    *,
    expert: ExpertProfile,
    uncovered_observations: list[dict[str, object]],
    max_findings: int,
    normalize_optional_line_value: Callable[[object], int | None],
) -> list[dict[str, object]]:
    if not uncovered_observations or max(1, int(max_findings or 1)) <= 0:
        return []

    forced: list[dict[str, object]] = []
    for item in uncovered_observations:
        kind = str(item.get("kind") or "").strip()
        file_path = str(item.get("file_path") or "").strip()
        line_start = int(normalize_optional_line_value(item.get("line_start")) or 1)
        observation_id = str(item.get("observation_id") or "").strip()
        evidence = [str(value).strip() for value in list(item.get("evidence") or []) if str(value).strip()]
        summary = str(item.get("summary") or "").strip()
        related_symbols = [str(value).strip() for value in list(item.get("related_symbols") or []) if str(value).strip()]
        symbol_display = " / ".join(related_symbols[:2]) if related_symbols else "当前调用"

        if expert.expert_id == "performance_reliability" and kind == "control_flow_with_external_call":
            forced.append(
                {
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_start,
                    "title": "循环调用放大",
                    "finding_type": "risk_hypothesis",
                    "claim": f"当前实现把外部依赖调用放进循环路径（{symbol_display}），批量场景会线性放大数据库/网络往返与整体时延。",
                    "severity": "high",
                    "matched_rules": [],
                    "violated_guidelines": [],
                    "rule_based_reasoning": "循环体内逐条调用仓储、远程服务或消息发送，会把单次调用成本放大到批量路径；如果批量输入来自外部请求或定时任务，应优先改为批量接口。",
                    "evidence": evidence[:3] or [summary or "检测到循环体中的外部依赖调用。"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [file_path] if file_path else [],
                    "observation_ids": [observation_id] if observation_id else [],
                    "fix_strategy": "把循环内逐条外部调用改成批量查询、批量远程接口或先聚合后统一处理。",
                    "suggested_fix": "优先把循环内的仓储/远程调用提到循环外，避免每个元素都触发一次外部依赖访问。",
                    "change_steps": ["把循环内仓储/远程调用移动到循环外", "改用批量查询、批量提交或先聚合后统一处理", "补充大批量输入下的调用次数和耗时回归用例"],
                    "suggested_code": "",
                    "confidence": min(max(normalize_confidence_value(item.get("confidence"), 0.0), 0.65), 0.78),
                    "verification_needed": True,
                    "verification_plan": "验证重点：核对循环内依赖调用次数、批量输入规模和外部依赖成本，确认是否按批量接口或批量提交修复。",
                    "direct_evidence": False,
                    "evidence_source": "observation_signal",
                }
            )
        elif expert.expert_id == "ddd_architecture" and kind in {
            "construction_path_changed",
            "aggregate_factory_bypass",
            "domain_event_ordering_risk",
        }:
            is_event_ordering = kind == "domain_event_ordering_risk"
            issue_type = "domain_event_ordering_risk" if is_event_ordering else "aggregate_factory_bypass"
            title = "领域事件发布早于聚合持久化" if is_event_ordering else "绕过聚合工厂创建聚合根"
            claim = (
                f"当前变更把领域事件发布放在聚合持久化之前（{symbol_display}），事件订阅方可能先看到尚未持久化成功的状态。"
                if is_event_ordering
                else f"当前变更把原聚合工厂创建路径改成直接 new 构造（{symbol_display}），会绕过工厂封装的不变量校验、领域事件记录或创建语义。"
            )
            reasoning = (
                "领域事件应在聚合状态持久化成功后再发布，否则事件消费者可能读到未提交或最终失败的数据。"
                if is_event_ordering
                else "聚合工厂通常封装创建不变量、默认值和领域事件记录；直接 new 聚合根会让这些领域语义失效。"
            )
            fix_strategy = (
                "恢复先创建聚合、保存聚合，再发布聚合产生的领域事件的顺序。"
                if is_event_ordering
                else "恢复聚合工厂创建入口，避免在应用服务中直接 new 聚合根。"
            )
            forced.append(
                {
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_start,
                    "title": title,
                    "finding_type": "direct_defect",
                    "normalized_issue_type": issue_type,
                    "claim": claim,
                    "severity": "high",
                    "matched_rules": ["DDD-JDDD-001", "ARCH-JDDD-002"],
                    "violated_guidelines": ["聚合创建和领域事件发布必须保持领域不变量、持久化顺序和事件一致性"],
                    "rule_based_reasoning": reasoning,
                    "evidence": evidence[:3] or [summary or "检测到对象创建路径发生变化。"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [file_path] if file_path else [],
                    "observation_ids": [observation_id] if observation_id else [],
                    "fix_strategy": fix_strategy,
                    "suggested_fix": "恢复原有聚合工厂或聚合静态工厂创建入口，先完成持久化，再发布该聚合产生的领域事件，并补充创建行为测试。",
                    "change_steps": ["恢复原有聚合工厂/静态工厂创建入口", "先保存聚合状态，再发布聚合产生的领域事件", "补充聚合创建、事件记录和持久化顺序的回归测试"],
                    "suggested_code": "",
                    "confidence": min(max(normalize_confidence_value(item.get("confidence"), 0.0), 0.82), 0.92),
                    "verification_needed": False,
                    "verification_plan": "",
                    "direct_evidence": True,
                    "evidence_source": "observation_signal",
                }
            )
        elif expert.expert_id == "correctness_business" and kind == "declared_intent_without_implementation":
            forced.append(
                {
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_start,
                    "title": "承诺未落地",
                    "finding_type": "risk_hypothesis",
                    "claim": f"注释、TODO 或方法意图已经承诺了行为（{symbol_display}），但当前实现没有对应动作，调用方会误以为能力已经落地。",
                    "severity": "high",
                    "matched_rules": ["CORR-JDDD-001", "CORRECTNESS-CONTRACT-001"],
                    "violated_guidelines": ["注释、TODO 或接口说明承诺的业务行为必须在实现中落地"],
                    "rule_based_reasoning": "注释、接口说明或 TODO 已经表达代码语义承诺；当实现中没有对应动作时，调用方会按已落地能力使用，容易造成业务结果缺失。",
                    "evidence": evidence[:3] or [summary or "检测到注释、TODO 或方法意图与实现不一致。"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [file_path] if file_path else [],
                    "observation_ids": [observation_id] if observation_id else [],
                    "fix_strategy": "要么补齐承诺中的行为，要么删除会误导调用方的注释、TODO 或命名表达。",
                    "suggested_fix": "如果该行为属于本次交付范围，请补齐对应业务动作并增加测试；如果不属于本次交付范围，应删除或改写会误导调用方的 TODO/注释。",
                    "change_steps": ["在当前方法内补齐 TODO/注释承诺的业务动作", "为该业务动作增加单元测试或集成测试", "同步更新注释、接口说明和方法命名，避免再次承诺未实现能力"],
                    "suggested_code": "",
                    "confidence": min(max(normalize_confidence_value(item.get("confidence"), 0.0), 0.65), 0.78),
                    "verification_needed": True,
                    "verification_plan": "验证重点：核对注释、TODO 或命名表达对应的业务动作是否已经在当前实现中落地。",
                    "direct_evidence": False,
                    "evidence_source": "observation_signal",
                }
            )

        if len(forced) >= max(1, int(max_findings or 1)):
            break

    return forced
