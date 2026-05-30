from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

import app.services.review_service as review_service_module

router = APIRouter()


class HumanDecisionRequest(BaseModel):
    """定义人工裁决提交时的请求体。"""

    issue_id: str
    decision: str
    comment: str


class ImpactFeedbackRequest(BaseModel):
    """定义关联影响分析反馈提交时的请求体。"""

    target_type: str
    target_key: str
    label: str
    comment: str = ""


class ExportIssuesToCodehubRequest(BaseModel):
    """定义 issue 导出到 CodeHub 的 mock 请求体。"""

    issue_ids: list[str] = Field(default_factory=list)


def _sanitize_export_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = {
        "确认被保护的共享资源": "恢复被删除的锁保护，或补充等价的幂等、唯一约束、分布式锁等并发控制。",
        "改成批量获取或批量提交": "把循环内逐条访问改为批量查询、批量保存或固定窗口批处理。",
        "补齐对应业务动作或副作用": "补齐 TODO 或注释承诺的业务动作，并增加覆盖该动作的测试。",
        "同步修正注释/TODO/接口说明": "同步更新注释、接口说明和方法命名，避免继续承诺未实现能力。",
        "定位承诺的目标行为": "补齐 TODO 或注释承诺的业务动作，并增加覆盖该动作的测试。",
        "在当前代码锚点补齐缺失的业务逻辑或保护逻辑": "",
        "按当前代码片段补齐缺失实现，并增加能复现该风险的回归测试。": "",
        "回到当前代码锚点，补齐被规则命中的真实业务逻辑或保护逻辑。": "",
        "识别批量输入规模": "",
        "用回归用例覆盖本次被命中的风险路径": "",
        "确认修复后问题代码和建议代码不再相同": "",
    }
    if text in replacements:
        return replacements[text]
    if "结算异常被静默吞掉后仍返回成功状态" in text:
        return "支付结算失败后仍返回成功"
    text = text.replace(
        "异常被静默吞掉后仍返回成功状态，无日志、无指标、无补偿动作",
        "catch 分支把支付网关异常转换成成功返回，缺少失败结果、日志或补偿动作。",
    )
    lower = text.lower()
    blocked_markers = (
        "replace with actual patched code",
        "placeholder",
        "请结合审核结论补充修复方案",
        "请先根据问题详情",
        "当前 issue 来自一条有代码证据的检视发现",
        "当前未生成",
        "后端未返回",
        "系统没有生成",
        "根据实际",
        "请结合实际",
        "需要特别确认",
        "需要确认",
        "需要对比",
        "确认原",
        "当前变更在",
        "当前代码锚点",
        "按当前代码片段",
        "补齐被规则命中的真实业务逻辑",
        "识别批量输入规模",
        "代码锚点单独修复",
        "定位候选代码行",
        "按命中的规则",
        "按命中规则",
        "伪代码",
        "占位",
    )
    if any(marker in lower for marker in blocked_markers):
        return ""
    vague_action_patterns = (
        r"^确认.*(?:依赖类型|目标行为|是否|条件)",
        r"^需要.*确认",
        r"^需.*确认",
    )
    if any(re.search(pattern, text) for pattern in vague_action_patterns):
        return ""
    return text


def _is_concrete_export_code(value: object) -> bool:
    code = str(value or "").strip()
    if not code:
        return False
    lower = code.lower()
    invalid_markers = (
        "replace with actual patched code",
        "placeholder",
        "当前未生成",
        "todo:",
        "伪代码",
        "占位",
        "根据实际",
        "请结合实际",
    )
    if any(marker in lower for marker in invalid_markers):
        return False
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    return bool(lines) and not all(line.startswith(("//", "#", "*")) for line in lines)


def _mentions_other_export_context(value: object, file_path: object) -> bool:
    text = str(value or "").strip().lower()
    path = str(file_path or "").strip().lower()
    if not text or not path:
        return False
    context_markers: dict[str, tuple[str, ...]] = {
        "paymentsettlementservice": ("coursecreator", "bulkenrollmentservice", "报名", "库存", "聚合构造", "course.create"),
        "bulkenrollmentservice": ("paymentsettlementservice", "coursecreator", "支付网关", "结算", "course.create"),
        "coursecreator": ("paymentsettlementservice", "bulkenrollmentservice", "支付网关", "报名", "库存"),
    }
    current = next((marker for marker in context_markers if marker in path), "")
    return bool(current and any(marker in text for marker in context_markers[current]))


def _safe_export_text(value: object, file_path: object = "") -> str:
    text = _sanitize_export_text(value)
    if text and _mentions_other_export_context(text, file_path):
        return ""
    return text


def _export_issue_family(issue: object) -> str:
    issue_type = str(getattr(issue, "normalized_issue_type", "") or getattr(issue, "finding_type", "") or "").lower()
    title = str(getattr(issue, "title", "") or "").lower()
    summary = str(getattr(issue, "summary", "") or "").lower()
    current_code = str(getattr(issue, "current_code", "") or "").lower()
    semantic_text = "\n".join([issue_type, title, summary])
    exception_code_signal = (
        any(token in current_code for token in ("catch", "runtimeexception", "ignored", "异常"))
        and any(token in current_code for token in ("return settlementresult.success", "success(", "返回成功", "静默吞", "吞掉"))
    )
    if any(token in issue_type for token in ("comment", "declared_intent", "promise")) or any(token in title for token in ("todo", "承诺", "未实现")):
        return "comment"
    if any(token in issue_type for token in ("lock", "concurr")) or any(token in title for token in ("锁", "并发")):
        return "lock"
    if any(token in issue_type for token in ("course_creation", "aggregate", "domain_event")) or any(token in title for token in ("聚合", "领域事件", "工厂")):
        return "course_creation"
    if any(token in semantic_text for token in ("query_bound", "unbounded", "pagerequest", "分页", "查询边界")) or any(token in title for token in ("分页", "边界", "limit")):
        return "query_boundary"
    if any(token in semantic_text for token in ("n_plus_one", "loop_call_amplification", "repository.save", "saveall", "循环", "逐条", "批量保存")):
        return "loop"
    if exception_code_signal or any(token in issue_type for token in ("exception", "swallowed")) or any(token in title for token in ("异常", "失败")):
        return "exception"
    return ""


def _text_matches_export_family(family: str, value: object) -> bool:
    text = str(value or "").lower().replace(" ", "")
    if not family or not text:
        return False
    tokens: dict[str, tuple[str, ...]] = {
        "exception": ("catch", "exception", "runtimeexception", "ignored", "异常", "返回成功", "settlementresult.success"),
        "comment": ("todo", "注释", "承诺", "未实现", "没有实现", "扣减库存"),
        "lock": ("synchronized", "lock", "锁", "并发保护"),
        "query_boundary": ("limit", "分页", "边界", "全量", "全表"),
        "loop": ("循环", "逐条", "repository.save", "saveall", "n+1", "批量"),
        "course_creation": ("course.create", "newcourse", "聚合工厂", "聚合根", "领域事件", "domainevent"),
    }
    return any(token in text for token in tokens.get(family, ()))


def _safe_family_export_text(family: str, value: object, file_path: object = "") -> str:
    text = _safe_export_text(value, file_path)
    if text == "补充或更新覆盖该规则的测试":
        text = {
            "exception": "补充异常分支不能返回成功的回归测试。",
            "loop": "补充大批量输入下的调用次数和耗时回归测试。",
            "query_boundary": "补充大数据量查询场景的回归测试。",
            "comment": "补充 TODO 或注释承诺业务动作的回归测试。",
            "lock": "补充并发提交或重复消费场景的回归测试。",
            "course_creation": "补充聚合创建、领域事件记录和发布顺序的回归测试。",
        }.get(family, "")
    if text and _text_matches_export_family(family, text):
        return text
    return ""


def _canonical_export_summary(issue: object, family: str) -> str:
    file_name = str(getattr(issue, "file_path", "") or "").replace("\\", "/").split("/")[-1] or "当前文件"
    line_start = getattr(issue, "line_start", None)
    line = f" 第 {line_start} 行" if line_start else ""
    current_code = str(getattr(issue, "current_code", "") or "").lower()
    if family == "exception":
        return f"{file_name}{line} 的 catch 分支把异常转成成功返回，调用方会把失败路径误认为处理成功。"
    if family == "comment":
        return f"{file_name}{line} 的注释或 TODO 已承诺业务动作，但当前实现没有对应代码。"
    if family == "lock":
        return f"{file_name}{line} 移除了原有并发保护，批量或并发调用时可能出现重复处理或状态竞争。"
    if family == "query_boundary":
        return f"{file_name}{line} 的查询缺少分页、LIMIT 或固定窗口边界，数据量放大后可能返回大结果集。"
    if family == "loop":
        return f"{file_name}{line} 在循环内逐条调用仓储、网关或保存接口，批量输入会被放大为 N 次外部访问。"
    if family == "course_creation":
        if "new course" in current_code or "course(" in current_code:
            return f"{file_name}{line} 绕过 Course.create 创建聚合根，原先由工厂封装的不变量校验或领域事件记录可能丢失。"
        return f"{file_name}{line} 的聚合创建流程存在领域语义风险，需要恢复聚合工厂封装的创建约束。"
    return ""


def _canonical_export_remediation(issue: object, family: str) -> str:
    file_name = str(getattr(issue, "file_path", "") or "").replace("\\", "/").split("/")[-1] or "当前文件"
    line_start = getattr(issue, "line_start", None)
    line = f" 第 {line_start} 行" if line_start else ""
    if family == "exception":
        return f"修改 {file_name}{line} 的 catch 分支：不要返回成功结果，改为抛出业务异常、返回明确失败结果或进入补偿流程，并保留原始异常信息。"
    if family == "comment":
        return f"补齐 {file_name}{line} 注释或 TODO 中承诺的业务动作；如果本次不交付，应删除误导性注释并拆出明确任务。"
    if family == "lock":
        return f"恢复 {file_name}{line} 被移除的并发保护，或补上等价的幂等校验、唯一约束、分布式锁等控制，并增加并发调用用例。"
    if family == "query_boundary":
        return f"为 {file_name}{line} 的查询补回分页、LIMIT 或固定批次窗口，并覆盖大数据量查询回归用例。"
    if family == "loop":
        return f"把 {file_name}{line} 循环内的逐条访问改为批量查询、批量保存或固定窗口批处理，避免批量输入放大为 N 次外部访问。"
    if family == "course_creation":
        return f"调整 {file_name}{line} 的聚合创建流程：通过 Course.create 创建聚合根，并保持先持久化、后发布领域事件。"
    return ""


def _canonical_export_title(issue: object, family: str) -> str:
    current_code = str(getattr(issue, "current_code", "") or "").lower()
    file_path = str(getattr(issue, "file_path", "") or "").lower()
    if family == "exception":
        return "支付结算失败后仍返回成功" if "payment" in file_path else "异常被吞掉后仍按成功处理"
    if family == "comment":
        return "TODO 里的库存扣减未实现" if "库存" in str(getattr(issue, "summary", "") or "") or "inventory" in current_code else "注释承诺未实现"
    if family == "lock":
        return "并发保护被移除"
    if family == "query_boundary":
        return "查询没有分页限制"
    if family == "loop":
        if "paymentsettlementservice" in file_path:
            return "支付批量结算从 saveAll 退化为循环逐条保存"
        if "bulkenrollmentservice" in file_path:
            return "批量报名从 saveAll 退化为循环逐条保存"
        return "批量保存改成了循环逐条保存"
    if family == "course_creation":
        if "new course" in current_code or "course(" in current_code:
            return "绕过聚合工厂创建聚合根"
        return "聚合创建流程存在领域语义风险"
    return _sanitize_export_text(getattr(issue, "title", "") or "") or "代码问题"


def _canonical_export_group_label(issue: object, family: str, value: object) -> str:
    """把专家合并后的短标签改成研发能直接理解的补充说明。"""

    text = _safe_export_text(value, getattr(issue, "file_path", ""))
    if not text:
        return ""
    compact = text.lower().replace(" ", "")
    if family == "exception" and any(token in compact for token in ("异常被静默吞掉", "异常被吞掉", "exceptionswallowed")):
        return "catch 分支没有把失败传递给调用方"
    if family == "comment" and any(token in compact for token in ("承诺未落地", "commentcontract", "declaredintent")):
        return "注释或 TODO 写了要做，但代码没有对应实现"
    if family == "lock" and any(token in compact for token in ("并发保护被移除", "lockguard", "concurrencyguard")):
        return "原有锁保护被删除，并发调用时缺少保护"
    if family == "query_boundary" and any(token in compact for token in ("查询边界缺失", "querybound", "unboundedquery")):
        return "查询缺少分页或 LIMIT 保护"
    if family == "loop" and any(token in compact for token in ("循环调用放大", "loopcall", "n+1")):
        return "循环内重复访问仓储或外部接口"
    if family == "course_creation" and any(token in compact for token in ("领域事件发布顺序", "aggregatefactory", "聚合工厂")):
        return "聚合创建没有走统一工厂入口"
    return text


def _export_summary_agrees_with_code(issue: object, family: str, summary: str) -> bool:
    if not summary:
        return False
    current_code = str(getattr(issue, "current_code", "") or "").lower()
    compact_summary = summary.lower().replace(" ", "")
    if any(token in compact_summary for token in ("需要特别确认", "需要确认", "需要复核", "需要对比", "确认原", "不确定是否")):
        return False
    if family == "exception":
        return any(token in current_code for token in ("catch", "runtimeexception", "ignored", "settlementresult.success"))
    if family == "query_boundary":
        return any(token in current_code for token in ("pagerequest", "pageable", "limit", "searchpendingbycourselike", "findpendingbycourse"))
    if family == "loop":
        return any(token in current_code for token in ("for (", ".foreach", "while (")) and any(
            token in current_code for token in ("repository.save", "paymentrepository.save", ".save(", "saveall")
        )
    if family == "course_creation":
        mentions_event_publish = any(token in compact_summary for token in ("发布领域事件", "发布事件", "发布顺序", "publish"))
        code_mentions_event_publish = any(token in current_code for token in ("publish", "eventbus", "domainevent"))
        if mentions_event_publish and not code_mentions_event_publish:
            return False
    return True


@router.get("/reviews/{review_id}/issues")
def list_issues(review_id: str) -> list[dict[str, object]]:
    """返回某次审核收敛后的 issue 列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_issues(review_id)
    ]


@router.get("/reviews/{review_id}/issues/{issue_id}/messages")
def list_issue_messages(review_id: str, issue_id: str) -> list[dict[str, object]]:
    """返回某个 issue 关联的消息流。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_issue_messages(review_id, issue_id)
    ]


@router.post("/reviews/{review_id}/human-decisions", status_code=status.HTTP_202_ACCEPTED)
def record_human_decision(review_id: str, payload: HumanDecisionRequest) -> dict[str, object]:
    """记录人工批准或驳回结果，并刷新审核状态。"""

    learning_effect = ""
    if payload.decision == "approved":
        learning_effect = "confirmed_sample"
    elif payload.decision == "rejected":
        learning_effect = "false_positive_sample"
    try:
        updated = review_service_module.review_service.record_human_decision(
            review_id, payload.issue_id, payload.decision, payload.comment
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review or issue not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="issue is not pending human decision") from error
    return {
        "review_id": updated.review_id,
        "status": updated.status,
        "phase": updated.phase,
        "human_review_status": updated.human_review_status,
        "learning_recorded": payload.decision in {"approved", "rejected"},
        "learning_effect": learning_effect,
    }


@router.post("/reviews/{review_id}/impact-feedback", status_code=status.HTTP_202_ACCEPTED)
def record_impact_feedback(review_id: str, payload: ImpactFeedbackRequest) -> dict[str, object]:
    """记录关联影响路径、文件或测试建议的确认/误报反馈。"""

    try:
        label = review_service_module.review_service.record_impact_feedback(
            review_id,
            target_type=payload.target_type,
            target_key=payload.target_key,
            label=payload.label,
            comment=payload.comment,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return label.model_dump(mode="json")


@router.post("/reviews/{review_id}/issues/export/codehub")
def export_issues_to_codehub(review_id: str, payload: ExportIssuesToCodehubRequest) -> dict[str, object]:
    """模拟将选中的正式议题提交到 CodeHub。"""

    review = review_service_module.review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    issues = review_service_module.review_service.list_issues(review_id)
    findings = review_service_module.review_service.list_display_findings(review_id)
    finding_by_id = {item.finding_id: item for item in findings}
    selected_issue_ids = [item for item in payload.issue_ids if item]
    exported_items: list[dict[str, object]] = []

    for issue in issues:
        if issue.issue_id not in selected_issue_ids:
            continue
        related_findings = [finding_by_id[item] for item in issue.finding_ids if item in finding_by_id]
        issue_family = _export_issue_family(issue)
        suggestion_parts: list[str] = []
        patched_code = ""
        for finding in related_findings:
            if finding.remediation_strategy:
                suggestion_parts.append(_safe_family_export_text(issue_family, finding.remediation_strategy, issue.file_path))
            if finding.remediation_suggestion:
                suggestion_parts.append(_safe_family_export_text(issue_family, finding.remediation_suggestion, issue.file_path))
            for step in finding.remediation_steps or []:
                if step:
                    suggestion_parts.append(_safe_family_export_text(issue_family, step, issue.file_path))
            if not patched_code and _is_concrete_export_code(finding.suggested_code):
                patched_code = finding.suggested_code
        if not suggestion_parts:
            suggestion_parts.extend(_safe_family_export_text(issue_family, item, issue.file_path) for item in (issue.aggregated_remediation_suggestions or []))
            suggestion_parts.extend(_safe_family_export_text(issue_family, item, issue.file_path) for item in (issue.aggregated_remediation_steps or []))
        if issue.remediation_strategy:
            suggestion_parts.insert(0, _safe_family_export_text(issue_family, issue.remediation_strategy, issue.file_path) or _safe_export_text(issue.remediation_strategy, issue.file_path))
        if issue.remediation_suggestion:
            suggestion_parts.insert(0, _safe_family_export_text(issue_family, issue.remediation_suggestion, issue.file_path) or _safe_export_text(issue.remediation_suggestion, issue.file_path))
        for step in issue.remediation_steps or []:
            if step:
                suggestion_parts.append(_safe_family_export_text(issue_family, step, issue.file_path))
        suggestion_parts = [item for item in suggestion_parts if item]
        if not suggestion_parts:
            canonical_remediation = _canonical_export_remediation(issue, issue_family)
            if canonical_remediation:
                suggestion_parts.append(canonical_remediation)
        if _is_concrete_export_code(issue.suggested_code):
            patched_code = issue.suggested_code

        issue_summary = _safe_family_export_text(issue_family, issue.summary, issue.file_path)
        if not _export_summary_agrees_with_code(issue, issue_family, issue_summary):
            issue_summary = _canonical_export_summary(issue, issue_family)
        issue_summary = issue_summary or _canonical_export_summary(issue, issue_family) or _safe_export_text(issue.summary, issue.file_path)
        related_evidence = []
        for finding in related_findings:
            evidence_text = _safe_family_export_text(issue_family, finding.summary, issue.file_path)
            if evidence_text and _export_summary_agrees_with_code(issue, issue_family, evidence_text):
                related_evidence.append(evidence_text)
        related_evidence = [item for item in dict.fromkeys(related_evidence) if item]
        aggregated_titles = []
        for title in issue.aggregated_titles:
            title_text = _safe_family_export_text(issue_family, title, issue.file_path) or _safe_export_text(title, issue.file_path)
            title_text = _canonical_export_group_label(issue, issue_family, title_text)
            if title_text and _export_summary_agrees_with_code(issue, issue_family, title_text):
                aggregated_titles.append(title_text)
        canonical_title = _canonical_export_title(issue, issue_family)
        aggregated_titles = [
            item
            for item in dict.fromkeys(aggregated_titles)
            if item and item.strip() != canonical_title.strip()
        ]
        description_parts = [issue_summary]
        if aggregated_titles:
            description_parts.extend(["其他专家补充：", *aggregated_titles[:4]])
        if related_evidence:
            description_parts.extend(["关联证据：", *related_evidence[:4]])
        problem_description = "\n\n".join(part for part in description_parts if part)
        exported_items.append(
            {
                "issue_id": issue.issue_id,
                "title": _canonical_export_title(issue, issue_family),
                "severity": issue.severity,
                "problem_description": problem_description,
                "remediation_suggestion": "\n".join(dict.fromkeys(suggestion_parts)),
                "patched_code": patched_code,
                "mock_ticket_url": f"mock://codehub/issues/{issue.issue_id}",
                "finding_ids": issue.finding_ids,
            }
        )

    return {
        "review_id": review_id,
        "status": "mock_submitted",
        "submitted_count": len(exported_items),
        "items": exported_items,
    }
