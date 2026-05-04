from __future__ import annotations

from typing import Callable

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject


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
    confidence = float(candidate.get("confidence") or 0.0)
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
        "3. 只有确认成立且不是首轮已输出重复问题时，才输出新的 finding；",
        "4. 每条新增 finding 必须带 file_path、line_start、line_end、claim、suggested_code、observation_ids；",
        '5. 如果没有新增问题，返回 {"findings":[]}。',
        f"6. 最多新增 {max(1, int(max_findings or 1))} 条 findings。",
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
            '仅输出 JSON：{"findings":[{...}]}',
            'finding 字段至少包含: file_path, line_start, line_end, title, finding_type, claim, evidence, fix_strategy, suggested_fix, change_steps, suggested_code, confidence, observation_ids',
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
                    "rule_based_reasoning": "循环体内逐条调用仓储、远程服务或消息发送，可能把单次调用成本放大到批量路径，需要结合调用规模和依赖成本复核。",
                    "evidence": evidence[:3] or [summary or "检测到循环体中的外部依赖调用。"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [file_path] if file_path else [],
                    "observation_ids": [observation_id] if observation_id else [],
                    "fix_strategy": "把循环内逐条外部调用改成批量查询、批量远程接口或先聚合后统一处理。",
                    "suggested_fix": "优先把循环内的仓储/远程调用提到循环外，避免每个元素都触发一次外部依赖访问。",
                    "change_steps": ["确认循环内调用的依赖类型", "改成批量获取或批量提交", "保留单次结果映射关系"],
                    "suggested_code": "// TODO: 将循环内逐条外部调用改为批量处理，避免调用放大",
                    "confidence": min(max(float(item.get("confidence") or 0.0), 0.65), 0.78),
                    "verification_needed": True,
                    "verification_plan": "该问题来自结构化观察信号，需要结合调用频率、批量规模和外部依赖成本复核后再升级为确定缺陷。",
                    "direct_evidence": False,
                    "evidence_source": "observation_signal",
                }
            )
        elif expert.expert_id == "ddd_architecture" and kind == "construction_path_changed":
            forced.append(
                {
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_start,
                    "title": "创建路径变更风险",
                    "finding_type": "risk_hypothesis",
                    "normalized_issue_type": "construction_path_changed",
                    "claim": f"当前变更改变了对象创建路径（{symbol_display}），需要复核原创建入口是否承载不变量校验、领域事件或其他副作用。",
                    "severity": "high",
                    "matched_rules": ["DDD-JDDD-001", "ARCH-JDDD-002"],
                    "violated_guidelines": ["领域对象创建入口变更时必须确认不变量、领域事件和副作用仍被保留"],
                    "rule_based_reasoning": "创建路径变化本身不是自动缺陷；只有原创建入口确实承载不变量、领域事件或副作用且新路径未保留时，才应升级为确定问题。",
                    "evidence": evidence[:3] or [summary or "检测到对象创建路径发生变化。"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [file_path] if file_path else [],
                    "observation_ids": [observation_id] if observation_id else [],
                    "fix_strategy": "对比原创建入口与新创建路径，确认不变量校验、领域事件和副作用是否仍然完整。",
                    "suggested_fix": "如果原创建入口承载关键领域逻辑，请恢复该入口或把等价逻辑迁移到新的创建路径；如果不承载关键逻辑，应在评审说明中明确。",
                    "change_steps": ["定位原创建入口的校验和副作用", "对比新路径是否保留等价逻辑", "补充创建路径变更的领域行为测试"],
                    "suggested_code": "// TODO: 对比原创建入口与新构造路径，保留不变量校验和领域事件语义",
                    "confidence": min(max(float(item.get("confidence") or 0.0), 0.65), 0.78),
                    "verification_needed": True,
                    "verification_plan": "该问题来自结构化观察信号，需要确认原创建入口是否确实承载不变量校验、领域事件记录或其他副作用。",
                    "direct_evidence": False,
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
                    "matched_rules": [],
                    "violated_guidelines": [],
                    "rule_based_reasoning": "注释、接口说明或 TODO 可能表达代码语义承诺；如果仍是有效业务契约且实现中没有对应动作，才应升级为业务正确性问题。",
                    "evidence": evidence[:3] or [summary or "检测到注释、TODO 或方法意图与实现不一致。"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [file_path] if file_path else [],
                    "observation_ids": [observation_id] if observation_id else [],
                    "fix_strategy": "要么补齐承诺中的行为，要么删除会误导调用方的注释、TODO 或命名表达。",
                    "suggested_fix": "先确认该承诺是否仍然成立；如果成立，补齐实现；如果不再成立，删除失效承诺并同步修正文档或方法命名。",
                    "change_steps": ["确认承诺的目标行为", "补齐对应业务动作或副作用", "同步修正注释/TODO/接口说明"],
                    "suggested_code": "// TODO: 补齐承诺中的业务动作，或删除失效承诺避免误导调用方",
                    "confidence": min(max(float(item.get("confidence") or 0.0), 0.65), 0.78),
                    "verification_needed": True,
                    "verification_plan": "该问题来自结构化观察信号，需要确认注释、TODO 或命名表达是否仍是当前有效业务契约。",
                    "direct_evidence": False,
                    "evidence_source": "observation_signal",
                }
            )

        if len(forced) >= max(1, int(max_findings or 1)):
            break

    return forced
