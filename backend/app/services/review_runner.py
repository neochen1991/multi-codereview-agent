from __future__ import annotations

import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.domain.models.event import ReviewEvent
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewSubject, ReviewTask
from app.repositories.file_event_repository import FileEventRepository
from app.repositories.file_finding_repository import FileFindingRepository
from app.repositories.file_issue_repository import FileIssueRepository
from app.repositories.file_message_repository import FileMessageRepository
from app.repositories.file_review_repository import FileReviewRepository
from app.services.artifact_service import ArtifactService, build_report_summary
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.expert_registry import ExpertRegistry
from app.services.llm_chat_service import LLMChatService
from app.services.main_agent_service import MainAgentService
from app.services.orchestrator.graph import build_review_graph
from app.services.runtime_settings_service import RuntimeSettingsService
from app.services.skill_gateway import SkillGateway

logger = logging.getLogger(__name__)


class ReviewRunner:
    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = Path(storage_root or settings.STORAGE_ROOT)
        self.review_repo = FileReviewRepository(self.storage_root)
        self.event_repo = FileEventRepository(self.storage_root)
        self.finding_repo = FileFindingRepository(self.storage_root)
        self.issue_repo = FileIssueRepository(self.storage_root)
        self.message_repo = FileMessageRepository(self.storage_root)
        self.registry = ExpertRegistry(self.storage_root / "experts")
        self.runtime_settings_service = RuntimeSettingsService(self.storage_root)
        self.artifact_service = ArtifactService(self.storage_root)
        self.diff_excerpt_service = DiffExcerptService()
        self.capability_service = ExpertCapabilityService()
        self.main_agent_service = MainAgentService()
        self.llm_chat_service = LLMChatService()
        self.skill_gateway = SkillGateway(self.storage_root)
        self.graph = build_review_graph()

    def bootstrap_demo_review(self) -> str:
        review_id = f"rev_{uuid4().hex[:8]}"
        task = ReviewTask(
            review_id=review_id,
            status="pending",
            subject=ReviewSubject(
                subject_type="mr",
                repo_id="repo_demo",
                project_id="proj_demo",
                source_ref="feature/demo",
                target_ref="main",
                title="Demo review",
            ),
            selected_experts=settings.DEFAULT_EXPERT_IDS,
        )
        self.review_repo.save(task)
        return review_id

    def list_events(self, review_id: str) -> list[ReviewEvent]:
        return self.event_repo.list(review_id)

    def run_once(self, review_id: str) -> ReviewTask:
        review = self.review_repo.get(review_id)
        if review is None:
            raise KeyError(review_id)

        review.status = "running"
        review.phase = "expert_review"
        if review.started_at is None:
            review.started_at = datetime.now(UTC)
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="review_started",
                phase="intake",
                message="代码审核任务已启动",
            )
        )

        runtime_settings = self.runtime_settings_service.get()
        selected_ids = review.selected_experts or settings.DEFAULT_EXPERT_IDS
        enabled_experts = self.registry.list_enabled()
        experts = [expert for expert in enabled_experts if expert.expert_id in selected_ids]
        logger.info(
            "review execution review_id=%s selected_experts=%s enabled_experts=%s matched_experts=%s",
            review.review_id,
            selected_ids,
            [expert.expert_id for expert in enabled_experts],
            [expert.expert_id for expert in experts],
        )
        if not experts:
            reason = (
                "没有可执行的专家，请检查预置专家是否已部署，或确认 selected_experts 与 enabled experts 是否匹配。"
            )
            logger.error(
                "review has no executable experts review_id=%s selected_experts=%s enabled_experts=%s",
                review.review_id,
                selected_ids,
                [expert.expert_id for expert in enabled_experts],
            )
            review.status = "failed"
            review.phase = "failed"
            review.failure_reason = reason
            review.report_summary = reason
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = max(
                0.0,
                round((review.completed_at - (review.started_at or review.created_at)).total_seconds(), 3),
            )
            review.updated_at = datetime.now(UTC)
            self.review_repo.save(review)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="review_failed",
                    phase="failed",
                    message=reason,
                    payload={
                        "selected_experts": selected_ids,
                        "enabled_experts": [expert.expert_id for expert in enabled_experts],
                    },
                )
            )
            return review

        experts_by_id = {expert.expert_id: expert for expert in experts}
        finding_payloads: list[dict[str, object]] = []
        expert_jobs: list[dict[str, object]] = []
        for expert in experts:
            command = self.main_agent_service.build_command(
                review.subject,
                expert,
                runtime_settings,
            )
            expert_id = expert.expert_id
            file_path = str(command.get("file_path") or self._pick_file_path(review.subject, expert_id))
            line_start = int(command.get("line_start") or 1)
            summary = str(command.get("summary") or "")
            llm_metadata = dict(command.get("llm") or {})
            if not bool(command.get("routeable", True)):
                skip_reason = str(command.get("skip_reason") or "当前变更未命中该专家的有效审查线索")
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review_id,
                        event_type="expert_skipped",
                        phase="coordination",
                        message=f"{expert.name_zh} 已跳过本轮审查",
                        payload={
                            "expert_id": expert_id,
                            "file_path": file_path,
                            "line_start": line_start,
                            "reason": skip_reason,
                        },
                    )
                )
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review_id,
                        issue_id="review_orchestration",
                        expert_id=expert_id,
                        message_type="expert_skipped",
                        content=f"{expert.name_zh} 已跳过本轮审查：{skip_reason}",
                        metadata={
                            "phase": "coordination",
                            "file_path": file_path,
                            "line_start": line_start,
                            "reason": skip_reason,
                            **llm_metadata,
                        },
                    )
                )
                continue
            command_message = self.message_repo.append(
                ConversationMessage(
                    review_id=review_id,
                    issue_id="review_orchestration",
                    expert_id=self.main_agent_service.agent_id,
                    message_type="main_agent_command",
                    content=summary,
                    metadata={
                        "phase": "coordination",
                        "target_expert_id": expert_id,
                        "target_expert_name": expert.name_zh,
                        "file_path": file_path,
                        "line_start": line_start,
                        "related_files": command.get("related_files", []),
                        "target_hunk": command.get("target_hunk", {}),
                        "repository_context": command.get("repository_context", {}),
                        "expected_checks": command.get("expected_checks", []),
                        "disallowed_inference": command.get("disallowed_inference", []),
                        **llm_metadata,
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="main_agent_command",
                    phase="coordination",
                    message=f"主Agent 已向 {expert.name_zh} 下发审查指令",
                    payload={
                        "target_expert_id": expert_id,
                        "target_expert_name": expert.name_zh,
                        "file_path": file_path,
                        "line_start": line_start,
                        "related_files": command.get("related_files", []),
                    },
                )
            )
            expert_jobs.append(
                {
                    "review": review,
                    "expert": expert,
                    "command_message": command_message,
                    "file_path": file_path,
                    "line_start": line_start,
                    "runtime_settings": runtime_settings,
                    "finding_payloads": finding_payloads,
                }
            )

        self._execute_expert_jobs(expert_jobs)

        graph_result = self.graph.invoke(
            {
                "review_id": review_id,
                "phase": "ingest",
                "subject_type": review.subject.subject_type,
                "changed_files": review.subject.changed_files,
                "unified_diff": review.subject.unified_diff,
                "selected_experts": selected_ids,
                "findings": finding_payloads,
            }
        )

        issues = [
            DebateIssue(
                review_id=review_id,
                issue_id=str(item.get("issue_id") or f"iss_{uuid4().hex[:12]}"),
                title=str(item.get("title") or "待裁决议题"),
                summary=str(item.get("summary") or ""),
                finding_type=str(item.get("finding_type") or "risk_hypothesis"),
                file_path=str(item.get("file_path") or ""),
                line_start=int(item.get("line_start") or 1),
                status=str(item.get("status") or "open"),
                severity=str(item.get("severity") or "medium"),
                confidence=float(item.get("confidence") or 0.72),
                finding_ids=[str(value) for value in item.get("finding_ids", [])],
                participant_expert_ids=[str(value) for value in item.get("participant_expert_ids", [])],
                evidence=[str(value) for value in item.get("evidence", [])],
                cross_file_evidence=[str(value) for value in item.get("cross_file_evidence", [])],
                assumptions=[str(value) for value in item.get("assumptions", [])],
                context_files=[str(value) for value in item.get("context_files", [])],
                direct_evidence=bool(item.get("direct_evidence")),
                needs_human=bool(item.get("needs_human")),
                verified=bool(item.get("verified")),
                needs_debate=bool(item.get("needs_debate")),
                verifier_name=str(item.get("verifier_name") or ""),
                tool_name=str(item.get("tool_name") or ""),
                tool_verified=bool(item.get("tool_verified")),
                resolution=str(item.get("resolution") or ""),
            )
            for item in graph_result.get("issues", [])
        ]
        self.issue_repo.save_all(review_id, issues)
        for issue in issues:
            self._persist_issue_thread(
                review=review,
                issue=issue,
                experts_by_id=experts_by_id,
                runtime_settings=runtime_settings,
            )

        pending_human_issue_ids = [issue.issue_id for issue in issues if issue.needs_human]
        if pending_human_issue_ids:
            review.status = "waiting_human"
            review.phase = "human_gate"
            review.human_review_status = "requested"
            review.pending_human_issue_ids = pending_human_issue_ids
            review.completed_at = None
            review.duration_seconds = None
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="human_gate_requested",
                    phase="human_gate",
                    message="高风险议题已提交人工复核",
                    payload={"issue_ids": pending_human_issue_ids},
                )
            )
        else:
            review.status = "completed"
            review.phase = "completed"
            review.human_review_status = "not_required"
            review.pending_human_issue_ids = []
            review.completed_at = datetime.now(UTC)
            review.duration_seconds = max(
                0.0,
                round((review.completed_at - (review.started_at or review.created_at)).total_seconds(), 3),
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="review_completed",
                    phase="completed",
                    message="代码审核任务已完成",
                )
            )

        review.report_summary = build_report_summary(
            review=review,
            finding_count=len(finding_payloads),
            issue_count=len(issues),
            pending_human_count=len(pending_human_issue_ids),
        )
        final_summary, final_llm = self.main_agent_service.build_final_summary(
            review,
            issues,
            runtime_settings,
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review_id,
                issue_id="review_orchestration",
                expert_id=self.main_agent_service.agent_id,
                message_type="main_agent_summary",
                content=final_summary,
                metadata={
                    "phase": "coordination",
                    "status": review.status,
                    "issue_count": len(issues),
                    "pending_human_count": len(pending_human_issue_ids),
                    **final_llm,
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review_id,
                event_type="main_agent_summary",
                phase="coordination",
                message="主Agent 已完成收敛总结",
                payload={
                    "issue_count": len(issues),
                    "pending_human_count": len(pending_human_issue_ids),
                    "status": review.status,
                },
            )
        )
        review.updated_at = datetime.now(UTC)
        self.review_repo.save(review)
        logger.info(
            "review finished review_id=%s status=%s finding_count=%s issue_count=%s pending_human=%s",
            review.review_id,
            review.status,
            len(finding_payloads),
            len(issues),
            len(pending_human_issue_ids),
        )
        self.artifact_service.publish(review, issues)
        return review

    def _execute_expert_jobs(self, expert_jobs: list[dict[str, object]]) -> None:
        if not expert_jobs:
            return
        if os.getenv("PYTEST_CURRENT_TEST") or len(expert_jobs) <= 1:
            for job in expert_jobs:
                self._run_expert_from_command(**job)
            return
        max_workers = min(4, len(expert_jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._run_expert_from_command, **job) for job in expert_jobs]
            for future in futures:
                future.result()

    def _run_expert_from_command(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        command_message: ConversationMessage,
        file_path: str,
        line_start: int,
        runtime_settings,
        finding_payloads: list[dict[str, object]],
    ) -> None:
        tool_evidence = self.capability_service.collect_tool_evidence(expert, review.subject)
        skill_results = self.skill_gateway.invoke_for_expert(
            expert,
            review.subject,
            runtime_settings,
            file_path=file_path,
            line_start=line_start,
            related_files=list(command_message.metadata.get("related_files", []) or []),
        )
        repository_context = dict(command_message.metadata.get("repository_context") or {})
        target_hunk = dict(command_message.metadata.get("target_hunk") or {})
        for tool_result in tool_evidence:
            tool_name = str(tool_result.get("tool_name") or "")
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_tool_call",
                    content=str(tool_result.get("summary") or f"{tool_name} 调用完成"),
                    metadata={
                        "phase": "expert_review",
                        "tool_name": tool_name,
                        "file_path": file_path,
                        "line_start": line_start,
                        "tool_result": tool_result,
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_tool_invoked",
                    phase="expert_review",
                    message=f"{expert.name_zh} 调用了 tool {tool_name}",
                    payload={"expert_id": expert.expert_id, "tool_name": tool_name},
                )
            )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_ack",
                content=(
                    f"收到，{expert.name_zh} 将先检查 {file_path} 第 {line_start} 行附近的变更，"
                    f"重点验证 {expert.focus_areas[0] if expert.focus_areas else expert.role}。"
                ),
                metadata={
                    "phase": "coordination",
                    "reply_to_message_id": command_message.message_id,
                    "reply_to_expert_id": self.main_agent_service.agent_id,
                    "file_path": file_path,
                    "line_start": line_start,
                    "allowed_tools": expert.tool_bindings,
                    "allowed_skills": expert.skill_bindings,
                    "knowledge_sources": expert.knowledge_sources,
                    "related_files": command_message.metadata.get("related_files", []),
                    "target_hunk": target_hunk,
                    "repository_context": repository_context,
                    "expected_checks": command_message.metadata.get("expected_checks", []),
                    "disallowed_inference": command_message.metadata.get("disallowed_inference", []),
                    "skill_results": skill_results,
                    **self._expert_llm_metadata(expert, runtime_settings),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="expert_started",
                phase="expert_review",
                message=f"{expert.name_zh} 收到主Agent指令后开始审查",
                payload={"expert_id": expert.expert_id, "file_path": file_path, "line_start": line_start},
            )
        )
        for skill_result in skill_results:
            skill_name = str(skill_result.get("skill_name") or "")
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id="review_orchestration",
                    expert_id=expert.expert_id,
                    message_type="expert_skill_call",
                    content=str(skill_result.get("summary") or f"{skill_name} 调用完成"),
                    metadata={
                        "phase": "expert_review",
                        "skill_name": skill_name,
                        "file_path": file_path,
                        "line_start": line_start,
                        "skill_result": skill_result,
                        "tool_name": skill_name,
                        "target_hunk": target_hunk,
                        **self._expert_llm_metadata(expert, runtime_settings),
                    },
                )
            )
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_skill_invoked",
                    phase="expert_review",
                    message=f"{expert.name_zh} 调用了 skill {skill_name}",
                    payload={"expert_id": expert.expert_id, "skill_name": skill_name},
                )
            )

        severity, confidence = self._score_finding(review.subject, expert.expert_id)
        llm_result = self.llm_chat_service.complete_text(
            system_prompt=self._build_expert_system_prompt(expert),
            user_prompt=self._build_expert_prompt(
                review.subject,
                expert,
                file_path,
                line_start,
                tool_evidence,
                skill_results,
                repository_context,
                target_hunk,
                list(command_message.metadata.get("disallowed_inference", []) or []),
                list(command_message.metadata.get("expected_checks", []) or []),
            ),
            resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
            runtime_settings=runtime_settings,
            fallback_text=self._build_expert_fallback(review.subject, expert, file_path, line_start),
            allow_fallback=self._allow_llm_fallback(runtime_settings),
        )
        parsed = self._parse_expert_analysis(
            llm_result.text,
            review.subject,
            expert,
            file_path,
            line_start,
        )
        parsed = self._stabilize_expert_analysis(parsed, file_path, line_start, target_hunk)
        severity = self._normalize_severity(parsed.get("severity"), severity)
        confidence = self._normalize_confidence(parsed.get("confidence"), confidence)
        parsed_line_start = self._normalize_line_start(parsed.get("line_start"), line_start)
        finding = ReviewFinding(
            review_id=review.review_id,
            expert_id=expert.expert_id,
            title=str(parsed.get("title") or self._build_finding_title(expert)),
            summary=str(parsed.get("claim") or self._build_finding_summary(review.subject, expert.expert_id)),
            finding_type=str(parsed.get("finding_type") or "risk_hypothesis"),
            severity=severity,
            confidence=confidence,
            file_path=file_path,
            line_start=parsed_line_start,
            evidence=self._build_evidence(review.subject, expert, file_path, tool_evidence, parsed),
            cross_file_evidence=[str(item).strip() for item in parsed.get("cross_file_evidence", []) if str(item).strip()],
            assumptions=[str(item).strip() for item in parsed.get("assumptions", []) if str(item).strip()],
            context_files=self._merge_context_files(
                parsed.get("context_files", []),
                repository_context,
                skill_results,
            ),
            verification_needed=bool(parsed.get("verification_needed", parsed.get("needs_verification", True))),
            verification_plan=str(parsed.get("verification_plan") or "").strip(),
            remediation_suggestion=str(
                parsed.get("suggested_fix")
                or self._build_remediation_suggestion(review.subject, expert.expert_id, file_path)
            ),
            code_excerpt=self._build_code_excerpt(
                review.subject,
                file_path,
                parsed_line_start,
                expert.expert_id,
            ),
        )
        self.finding_repo.save(review.review_id, finding)
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id=finding.finding_id,
                expert_id=expert.expert_id,
                message_type="expert_analysis",
                content=llm_result.text.strip(),
                metadata={
                    "phase": "expert_review",
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "reply_to_expert_id": self.main_agent_service.agent_id,
                    "reply_to_message_id": command_message.message_id,
                    "target_expert_id": expert.expert_id,
                    "allowed_tools": expert.tool_bindings,
                    "allowed_skills": expert.skill_bindings,
                    "knowledge_sources": expert.knowledge_sources,
                    "tool_evidence": tool_evidence,
                    "skill_results": skill_results,
                    "target_hunk": target_hunk,
                    "repository_context": repository_context,
                    "finding_type": finding.finding_type,
                    "context_files": finding.context_files,
                    "assumptions": finding.assumptions,
                    **self._llm_message_metadata(llm_result),
                },
            )
        )
        finding_payloads.append(finding.model_dump(mode="json"))
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="finding_created",
                phase="expert_review",
                message=f"{expert.name_zh} 生成审核发现",
                payload={"finding_id": finding.finding_id, "expert_id": expert.expert_id},
            )
        )

    def _persist_issue_thread(
        self,
        *,
        review: ReviewTask,
        issue: DebateIssue,
        experts_by_id: dict[str, ExpertProfile],
        runtime_settings,
    ) -> None:
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="debate_issue_created",
                phase="debate",
                message=f"{issue.title} 已进入议题池",
                payload={"issue_id": issue.issue_id, "status": issue.status},
            )
        )
        debate_participants = issue.participant_expert_ids[:2] or ["correctness_business", "architecture_design"]
        debate_participants = [item for item in debate_participants if item in experts_by_id] or list(experts_by_id)[:2]
        previous_expert_id = self.main_agent_service.agent_id
        issue_file_path = issue.file_path or self._pick_file_path(review.subject, debate_participants[0] if debate_participants else "correctness_business")
        issue_line_start = issue.line_start or self.diff_excerpt_service.find_nearest_line(
            review.subject.unified_diff,
            issue_file_path,
            1,
        ) or 1
        if issue.needs_debate and debate_participants:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="debate_started",
                    phase="debate",
                    message=f"{issue.title} 开始定向辩论",
                    payload={"issue_id": issue.issue_id, "participants": debate_participants},
                )
            )
            for index, participant_id in enumerate(debate_participants):
                expert = experts_by_id.get(participant_id)
                if expert is None:
                    continue
                file_path = issue_file_path
                line_start = issue_line_start
                llm_result = self.llm_chat_service.complete_text(
                    system_prompt=self._build_expert_system_prompt(expert),
                    user_prompt=self._build_debate_prompt(
                        review.subject,
                        issue,
                        expert,
                        previous_expert_id,
                        file_path,
                        line_start,
                    ),
                    resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
                    runtime_settings=runtime_settings,
                    fallback_text=self._build_debate_fallback(
                        issue,
                        expert,
                        previous_expert_id,
                        file_path,
                        line_start,
                    ),
                    allow_fallback=self._allow_llm_fallback(runtime_settings),
                )
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review.review_id,
                        issue_id=issue.issue_id,
                        expert_id=participant_id,
                        message_type="debate_message",
                        content=llm_result.text.strip(),
                        metadata={
                            "phase": "debate",
                            "issue_status": issue.status,
                            "resolution": issue.resolution,
                            "file_path": file_path,
                            "line_start": line_start,
                            "reply_to_expert_id": previous_expert_id,
                            "debate_turn": index + 1,
                            **self._llm_message_metadata(llm_result),
                        },
                    )
                )
                self.event_repo.append(
                    ReviewEvent(
                        review_id=review.review_id,
                        event_type="debate_message",
                        phase="debate",
                        message=f"{expert.name_zh} 提交了辩论意见",
                        payload={"issue_id": issue.issue_id, "expert_id": participant_id},
                    )
                )
                previous_expert_id = participant_id

        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id=issue.issue_id,
                expert_id="judge",
                message_type="judge_summary",
                content=issue.summary,
                metadata={
                    "phase": "judge",
                    "status": issue.status,
                    "needs_human": issue.needs_human,
                    "verified": issue.verified,
                    "file_path": issue_file_path,
                    "line_start": issue_line_start,
                    "reply_to_expert_id": previous_expert_id,
                },
            )
        )

    def _expert_llm_metadata(self, expert: ExpertProfile, runtime_settings) -> dict[str, object]:
        resolution = self.llm_chat_service.resolve_expert(expert, runtime_settings)
        return {
            "provider": resolution.provider,
            "model": resolution.model,
            "base_url": resolution.base_url,
            "api_key_env": resolution.api_key_env,
            "mode": "pending",
        }

    def _llm_message_metadata(self, llm_result) -> dict[str, object]:
        return {
            "provider": llm_result.provider,
            "model": llm_result.model,
            "base_url": llm_result.base_url,
            "api_key_env": llm_result.api_key_env,
            "mode": llm_result.mode,
            "llm_error": llm_result.error,
        }

    def _score_finding(self, subject: ReviewSubject, expert_id: str) -> tuple[str, float]:
        file_blob = " ".join(subject.changed_files).lower()
        if expert_id == "security_compliance" and any(
            token in file_blob for token in ["auth", "security", "permission", "token"]
        ):
            return "blocker", 0.91
        if expert_id == "database_analysis" and any(
            token in file_blob for token in ["migration", "sql", "schema", "db", "repository"]
        ):
            return "high", 0.88
        if expert_id == "redis_analysis" and any(token in file_blob for token in ["redis", "cache"]):
            return "high", 0.87
        if expert_id == "mq_analysis" and any(
            token in file_blob for token in ["mq", "kafka", "rocketmq", "rabbit", "queue", "consumer", "producer"]
        ):
            return "high", 0.87
        if expert_id == "ddd_specification":
            return "medium", 0.83
        if expert_id == "performance_reliability" and any(
            token in file_blob for token in ["migration", "sql", "repository", "db"]
        ):
            return "high", 0.86
        if expert_id == "test_verification":
            return "medium", 0.75
        if expert_id == "maintainability_code_health":
            return "low", 0.7
        return "medium", 0.8

    def _pick_file_path(self, subject: ReviewSubject, expert_id: str) -> str:
        if not subject.changed_files:
            return "src/example.ts"
        file_blob = " ".join(subject.changed_files).lower()
        if expert_id == "security_compliance":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["auth", "security", "permission", "token"]):
                    return file_path
        if expert_id == "performance_reliability":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "repository", "db"]):
                    return file_path
        if expert_id == "database_analysis":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "schema", "db", "repository", "dao"]):
                    return file_path
        if expert_id == "redis_analysis":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["redis", "cache"]):
                    return file_path
        if expert_id == "mq_analysis":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["mq", "kafka", "rocketmq", "rabbit", "queue", "consumer", "producer"]):
                    return file_path
        if expert_id == "ddd_specification":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["domain", "aggregate", "entity", "repository", "service", "application"]):
                    return file_path
        if expert_id == "test_verification":
            for file_path in subject.changed_files:
                if any(token in file_path.lower() for token in ["test", "spec", "playwright", "jest", "vitest"]):
                    return file_path
        if "frontend" in file_blob:
            for file_path in subject.changed_files:
                if "frontend" in file_path.lower():
                    return file_path
        return subject.changed_files[0]

    def _pick_line_start(self, subject: ReviewSubject, expert_id: str) -> int:
        file_blob = " ".join(subject.changed_files).lower()
        preferred_line = 12
        if expert_id == "security_compliance":
            preferred_line = 18
        elif expert_id == "architecture_design":
            preferred_line = 42 if "service" in file_blob or "runtime" in file_blob else 24
        elif expert_id == "performance_reliability":
            preferred_line = 57
        elif expert_id == "database_analysis":
            preferred_line = 36
        elif expert_id == "redis_analysis":
            preferred_line = 28
        elif expert_id == "mq_analysis":
            preferred_line = 30
        elif expert_id == "ddd_specification":
            preferred_line = 40
        elif expert_id == "test_verification":
            preferred_line = 73

        file_path = self._pick_file_path(subject, expert_id)
        return self.diff_excerpt_service.find_nearest_line(
            subject.unified_diff,
            file_path,
            preferred_line,
        ) or preferred_line

    def _build_evidence(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        tool_evidence: list[dict[str, object]],
        parsed: dict[str, object],
    ) -> list[str]:
        evidence = [expert.focus_areas[0] if expert.focus_areas else expert.role]
        for item in parsed.get("evidence", []):
            text = str(item).strip()
            if text:
                evidence.append(text)
        lowered_file_path = file_path.lower()
        if expert.expert_id in {"database_analysis", "performance_reliability"} or any(
            token in lowered_file_path for token in ["migration", ".sql", "schema", "db", "repository"]
        ):
            evidence.append("database_migration")
        if expert.expert_id == "security_compliance" or any(
            token in lowered_file_path for token in ["auth", "security", "permission", "token", "secret"]
        ):
            evidence.append("security_surface")
        if expert.expert_id == "test_verification" or any(
            token in lowered_file_path for token in ["test", "spec", "jest", "vitest", "playwright"]
        ):
            evidence.append("test_surface")
        for tool_result in tool_evidence:
            tool_name = str(tool_result.get("tool_name") or "")
            summary = str(tool_result.get("summary") or "").strip()
            evidence.append(f"{tool_name}:{summary}" if summary else tool_name)
        deduped: list[str] = []
        for item in evidence:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    def _build_finding_summary(self, subject: ReviewSubject, expert_id: str) -> str:
        file_blob = ", ".join(subject.changed_files[:2]) or subject.source_ref
        if expert_id == "security_compliance":
            return f"鉴权或敏感路径变更涉及 {file_blob}，当前实现没有充分体现权限边界、失败路径或敏感数据保护。"
        if expert_id == "performance_reliability":
            return f"数据访问或迁移路径变更涉及 {file_blob}，当前实现对锁粒度、回滚策略或资源影响的处理不完整。"
        if expert_id == "database_analysis":
            return f"数据库相关变更涉及 {file_blob}，当前实现对 schema 演进、索引影响、事务边界或回滚路径说明不足。"
        if expert_id == "redis_analysis":
            return f"缓存路径变更涉及 {file_blob}，当前实现对 key 设计、过期策略、一致性或击穿保护说明不足。"
        if expert_id == "mq_analysis":
            return f"消息链路变更涉及 {file_blob}，当前实现对消息顺序、幂等、重试和死信处理交代不足。"
        if expert_id == "ddd_specification":
            return f"领域建模相关改动涉及 {file_blob}，当前实现可能混淆领域规则、应用编排和基础设施职责，偏离 DDD 分层。"
        if expert_id == "test_verification":
            return f"当前改动涉及 {file_blob}，缺少与改动风险相匹配的回归测试或更强断言保护。"
        if expert_id == "architecture_design":
            return f"当前改动涉及 {file_blob}，模块边界、依赖方向或抽象层级出现了收缩，后续扩展成本会被放大。"
        if expert_id == "maintainability_code_health":
            return f"当前改动涉及 {file_blob}，实现把规则和流程揉在一起，后续维护和排错成本偏高。"
        return f"当前改动涉及 {file_blob}，存在需要进一步修正的实现风险，当前写法缺少足够的边界说明与保护。"

    def _build_finding_title(self, expert: ExpertProfile) -> str:
        if expert.expert_id == "security_compliance":
            return "权限与敏感路径保护不足"
        if expert.expert_id == "performance_reliability":
            return "资源与回滚控制存在风险"
        if expert.expert_id == "database_analysis":
            return "数据库演进与事务边界存在风险"
        if expert.expert_id == "redis_analysis":
            return "缓存一致性与失效策略存在风险"
        if expert.expert_id == "mq_analysis":
            return "消息幂等与重试语义存在风险"
        if expert.expert_id == "ddd_specification":
            return "领域边界与职责分层偏离 DDD 规范"
        if expert.expert_id == "test_verification":
            return "缺少与改动匹配的验证保护"
        if expert.expert_id == "architecture_design":
            return "模块边界与抽象层次被削弱"
        if expert.expert_id == "maintainability_code_health":
            return "实现耦合偏高，维护成本上升"
        return f"{expert.name_zh} 识别到待修复问题"

    def _build_remediation_suggestion(
        self,
        subject: ReviewSubject,
        expert_id: str,
        file_path: str,
    ) -> str:
        if expert_id == "security_compliance":
            return f"在 {file_path} 增加明确的权限校验、失败分支和敏感字段保护，并补充拒绝场景测试。"
        if expert_id == "performance_reliability":
            return f"在 {file_path} 拆出显式的回滚与超时控制，补充资源释放和慢路径保护。"
        if expert_id == "database_analysis":
            return f"在 {file_path} 明确事务边界、回滚策略和索引影响，并补充 schema 变更验证与回退脚本。"
        if expert_id == "redis_analysis":
            return f"在 {file_path} 明确 key 设计、TTL、一致性策略和缓存失效路径，并补充击穿/脏读保护。"
        if expert_id == "mq_analysis":
            return f"在 {file_path} 补充消息幂等键、重试上限、死信处理和消费顺序约束。"
        if expert_id == "ddd_specification":
            return f"在 {file_path} 把领域规则、应用服务编排和基础设施访问重新分层，避免聚合职责外溢。"
        if expert_id == "test_verification":
            return f"围绕 {file_path} 的关键分支补充回归测试，并为异常路径增加断言。"
        if expert_id == "architecture_design":
            return f"把 {file_path} 中的规则判断与执行流程解耦，收敛依赖方向，避免跨层直接耦合。"
        if expert_id == "maintainability_code_health":
            return f"把 {file_path} 中的条件分支和魔法值提取成独立函数或策略对象，降低后续维护成本。"
        return f"重构 {file_path} 的当前实现，补充边界保护与必要注释，并为关键路径增加测试。"

    def _build_code_excerpt(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        expert_id: str,
    ) -> str:
        excerpt = self.diff_excerpt_service.extract_excerpt(subject.unified_diff, file_path, line_start)
        if excerpt:
            return excerpt
        return self._build_fallback_code_excerpt(file_path, line_start, expert_id)

    def _build_fallback_code_excerpt(
        self,
        file_path: str,
        line_start: int,
        expert_id: str,
    ) -> str:
        lines = [
            f"{line_start:>4} | def review_guard(payload):",
            f"{line_start + 1:>4} |     if payload.get('enabled'):",
            f"{line_start + 2:>4} |         return True",
            f"{line_start + 3:>4} |     return False",
        ]
        if expert_id == "security_compliance":
            lines = [
                f"{line_start:>4} | def review_guard(payload, user):",
                f"{line_start + 1:>4} |     if payload.get('enabled'):",
                f"{line_start + 2:>4} |         return True",
                f"{line_start + 3:>4} |     return False  # missing permission check",
            ]
        if expert_id == "architecture_design":
            lines = [
                f"{line_start:>4} | def review_guard(payload):",
                f"{line_start + 1:>4} |     service = RuntimeService()",
                f"{line_start + 2:>4} |     service.repo.save(payload)",
                f"{line_start + 3:>4} |     return service.policy.allow(payload)",
            ]
        if expert_id == "performance_reliability":
            lines = [
                f"{line_start:>4} | def load_reviews(db):",
                f"{line_start + 1:>4} |     rows = db.query('select * from reviews')",
                f"{line_start + 2:>4} |     return [hydrate(row) for row in rows]",
                f"{line_start + 3:>4} |     # missing timeout / batching / rollback handling",
            ]
        if expert_id == "test_verification":
            lines = [
                f"{line_start:>4} | def review_guard(payload):",
                f"{line_start + 1:>4} |     if payload.get('enabled'):",
                f"{line_start + 2:>4} |         return True",
                f"{line_start + 3:>4} |     return False  # no regression test covers this branch",
            ]
        return f"# {file_path}\n" + "\n".join(lines)

    def _build_expert_prompt(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        tool_evidence: list[dict[str, object]],
        skill_results: list[dict[str, object]],
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
        disallowed_inference: list[str],
        expected_checks: list[str],
    ) -> str:
        capability_summary = self.capability_service.build_capability_summary(expert, tool_evidence)
        code_excerpt = self._build_code_excerpt(subject, file_path, line_start, expert.expert_id)
        skill_summary = self._build_skill_summary(skill_results)
        repository_context_summary = self._build_repository_context_summary(repository_context, skill_results)
        hunk_summary = self._build_hunk_summary(target_hunk)
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"专家: {expert.expert_id} / {expert.name_zh}\n"
            f"角色: {expert.role}\n"
            f"目标文件: {file_path}\n"
            f"目标行号: {line_start}\n"
            f"变更文件: {', '.join(subject.changed_files[:5]) or '未提供'}\n"
            f"能力约束:\n{capability_summary}\n"
            f"目标 hunk:\n{hunk_summary}\n"
            f"Skill 调用结果:\n{skill_summary}\n"
            f"代码仓上下文:\n{repository_context_summary}\n"
            f"当前代码片段:\n{code_excerpt}\n"
            f"必查项: {' / '.join(expected_checks[:5]) or expert.role}\n"
            f"禁止推断: {' / '.join(disallowed_inference[:5]) or '证据不足时只能输出待验证风险'}\n"
            f"请基于真实 diff 做审查，避免泛泛而谈，不要评论未涉及的文件，不要越过你的职责边界。\n"
            f"如果你的结论依赖“当前 diff 没显示某段代码”“可能存在未注入/未调用/未校验”，"
            f"你必须把它标记为 risk_hypothesis，并写入 assumptions 和 verification_plan，不能输出 direct_defect。\n"
            f"你必须只输出一个 JSON 对象，不要输出 Markdown，不要输出额外解释。\n"
            f"JSON 字段要求:\n"
            f'{{"ack":"先回应主Agent派工","title":"一句话问题标题","finding_type":"direct_defect|risk_hypothesis|test_gap|design_concern","claim":"必须落在当前文件/行号的风险结论","severity":"blocker|high|medium|low","line_start":{line_start},"line_end":{line_start},"evidence":["至少2条具体代码证据"],"cross_file_evidence":["跨文件佐证"],"assumptions":["若有推断必须写明"],"context_files":["引用的目标分支文件"],"why_it_matters":"影响说明","suggested_fix":"可执行修复建议","confidence":0.0,"verification_needed":true,"verification_plan":"需要如何继续验证"}}'
        )

    def _build_expert_system_prompt(self, expert: ExpertProfile) -> str:
        base_prompt = expert.system_prompt or f"你是{expert.name_zh}，你的职责是{expert.role}。"
        return (
            f"{base_prompt}\n\n"
            f"执行纪律：\n"
            f"1. 只在你的职责边界内下结论。\n"
            f"2. 结论必须绑定具体文件和代码行，禁止泛化空谈。\n"
            f"3. 没有代码证据时，只能提出“需要验证”，不能伪造确定性结论。\n"
            f"4. 修复建议必须可执行，不能只写“建议优化”。\n"
            f"5. 输出必须遵守 JSON contract。"
        )

    def _parse_expert_analysis(
        self,
        text: str,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
    ) -> dict[str, object]:
        parsed = self._parse_json_object(text)
        if parsed:
            return parsed
        return {
            "ack": self._extract_structured_field(text, "回应主Agent"),
            "title": self._extract_structured_field(text, "问题标题") or self._build_finding_title(expert),
            "claim": self._extract_structured_field(text, "风险结论")
            or self._build_finding_summary(subject, expert.expert_id),
            "finding_type": "risk_hypothesis",
            "severity": "",
            "line_start": line_start,
            "line_end": line_start,
            "evidence": [self._extract_structured_field(text, "代码证据")] if self._extract_structured_field(text, "代码证据") else [],
            "cross_file_evidence": [],
            "assumptions": [],
            "context_files": [],
            "why_it_matters": self._extract_structured_field(text, "证据诉求"),
            "suggested_fix": self._extract_structured_field(text, "修复建议")
            or self._build_remediation_suggestion(subject, expert.expert_id, file_path),
            "confidence": 0.0,
            "verification_needed": True,
            "verification_plan": "需要补充关联上下文、调用链和测试证据。",
        }

    def _stabilize_expert_analysis(
        self,
        parsed: dict[str, object],
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
    ) -> dict[str, object]:
        result = dict(parsed)
        text_blob = "\n".join(
            [
                str(result.get("title") or ""),
                str(result.get("claim") or ""),
                *[str(item) for item in list(result.get("evidence") or [])],
                *[str(item) for item in list(result.get("assumptions") or [])],
            ]
        )
        excerpt = str(target_hunk.get("excerpt") or "")
        normalized_excerpt_lines = []
        for line in excerpt.splitlines():
            cleaned = line
            if "|" in cleaned:
                cleaned = cleaned.split("|", 1)[1]
            cleaned = cleaned.strip()
            normalized_excerpt_lines.append(cleaned)
        import_only_excerpt = bool(excerpt) and all(
            line.startswith(("+import", "-import", "import ")) or not line
            for line in normalized_excerpt_lines
        )
        speculative_tokens = ["未显示", "未看到", "可能", "若", "如果", "假设", "推测"]
        import_inference_tokens = ["constructor", "注入", "依赖缺失", "未注入", "Cannot resolve dependency"]
        has_speculative_language = any(token in text_blob for token in speculative_tokens)
        has_import_inference = any(token in text_blob for token in import_inference_tokens)
        if import_only_excerpt and has_import_inference:
            result["finding_type"] = "risk_hypothesis"
            result["verification_needed"] = True
            result["verification_plan"] = (
                str(result.get("verification_plan") or "").strip()
                or "需要检查完整类定义和 constructor 注入，不能仅凭 import 变化下结论。"
            )
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            assumption = "当前结论基于 import 变化推断，尚未看到完整类定义与 constructor。"
            if assumption not in assumptions:
                assumptions.append(assumption)
            result["assumptions"] = assumptions
            result["confidence"] = min(float(result.get("confidence") or 0.0), 0.45)
            if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                result["severity"] = "medium"
        elif has_speculative_language and str(result.get("finding_type") or "") == "direct_defect":
            result["finding_type"] = "risk_hypothesis"
            result["verification_needed"] = True
            result["confidence"] = min(float(result.get("confidence") or 0.0), 0.6)

        result["line_start"] = self._normalize_line_start(result.get("line_start"), line_start)
        result["line_end"] = self._normalize_line_start(result.get("line_end"), int(result["line_start"]))
        result["context_files"] = [str(item).strip() for item in list(result.get("context_files") or []) if str(item).strip()]
        result["evidence"] = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        return result

    def _build_expert_fallback(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
    ) -> str:
        summary = self._build_finding_summary(subject, expert.expert_id)
        return (
            f"回应主Agent：收到，我先看 {file_path}:{line_start} 附近的改动。\n"
            f"问题标题：{self._build_finding_title(expert)}\n"
            f"风险结论：从{expert.name_zh}视角看，这里最值得警惕的是：{summary}\n"
            f"代码证据：我已经基于 diff 片段、绑定 skill 和知识库命中结果完成首轮取证，但仍需补充更直接的上下文证据。\n"
            f"修复建议：{self._build_remediation_suggestion(subject, expert.expert_id, file_path)}\n"
            f"证据诉求：需要补充关联测试、失败路径和变更前后的行为对比。"
        )

    def _build_skill_summary(self, skill_results: list[dict[str, object]]) -> str:
        if not skill_results:
            return "无可用 skill 或本轮未命中可调用 skill。"
        lines: list[str] = []
        for item in skill_results:
            skill_name = str(item.get("skill_name") or "")
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- {skill_name}: {summary}")
            else:
                lines.append(f"- {skill_name}: 已执行")
            matches = item.get("matches")
            if isinstance(matches, list) and matches:
                for match in matches[:2]:
                    if isinstance(match, dict):
                        title = str(match.get("title") or match.get("doc_id") or "knowledge")
                        snippet = str(match.get("snippet") or "").strip()
                        lines.append(f"  * {title}: {snippet[:160]}")
        return "\n".join(lines)

    def _build_repository_context_summary(
        self,
        repository_context: dict[str, object],
        skill_results: list[dict[str, object]],
    ) -> str:
        lines: list[str] = []
        if repository_context:
            summary = str(repository_context.get("summary") or "").strip()
            if summary:
                lines.append(f"- 主Agent上下文: {summary}")
            primary_context = repository_context.get("primary_context")
            if isinstance(primary_context, dict) and primary_context.get("snippet"):
                lines.append(f"- 目标文件: {primary_context.get('path')}")
            related_contexts = repository_context.get("related_contexts")
            if isinstance(related_contexts, list) and related_contexts:
                related_paths = [
                    str(item.get("path") or "").strip()
                    for item in related_contexts
                    if isinstance(item, dict) and str(item.get("path") or "").strip()
                ]
                if related_paths:
                    lines.append(f"- 关联文件: {' / '.join(related_paths[:4])}")
            symbol_contexts = repository_context.get("symbol_contexts")
            if isinstance(symbol_contexts, list) and symbol_contexts:
                for item in symbol_contexts[:2]:
                    if not isinstance(item, dict):
                        continue
                    symbol = str(item.get("symbol") or "").strip()
                    definition_count = len(list(item.get("definitions") or []))
                    reference_count = len(list(item.get("references") or []))
                    if symbol:
                        lines.append(f"- 符号上下文: {symbol} · 定义 {definition_count} · 引用 {reference_count}")
        for item in skill_results:
            if str(item.get("skill_name") or "") != "repo_context_search":
                continue
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- Repo skill: {summary}")
            context_files = [
                str(value).strip()
                for value in list(item.get("context_files") or [])
                if str(value).strip()
            ]
            if context_files:
                lines.append(f"- Repo 引用文件: {' / '.join(context_files[:4])}")
            matches = item.get("matches")
            if isinstance(matches, list) and matches:
                formatted = []
                for match in matches[:3]:
                    if isinstance(match, dict):
                        path = str(match.get("path") or "").strip()
                        line_number = match.get("line_number")
                        if path:
                            formatted.append(f"{path}:{line_number}")
                if formatted:
                    lines.append(f"- 代码仓命中: {' / '.join(formatted)}")
            symbol_contexts = item.get("symbol_contexts")
            if isinstance(symbol_contexts, list) and symbol_contexts:
                for symbol_context in symbol_contexts[:2]:
                    if not isinstance(symbol_context, dict):
                        continue
                    symbol = str(symbol_context.get("symbol") or "").strip()
                    if not symbol:
                        continue
                    lines.append(
                        f"- Repo 符号: {symbol} · 定义 {len(list(symbol_context.get('definitions') or []))} · "
                        f"引用 {len(list(symbol_context.get('references') or []))}"
                    )
        return "\n".join(lines) if lines else "未补充代码仓上下文。"

    def _build_hunk_summary(self, target_hunk: dict[str, object]) -> str:
        if not target_hunk:
            return "未定位到明确 hunk，请结合当前代码片段谨慎判断。"
        header = str(target_hunk.get("hunk_header") or "").strip()
        excerpt = str(target_hunk.get("excerpt") or "").strip()
        lines = []
        if header:
            lines.append(header)
        if excerpt:
            excerpt_lines = excerpt.splitlines()
            lines.extend(excerpt_lines[:8])
        return "\n".join(lines) if lines else "未定位到明确 hunk，请结合当前代码片段谨慎判断。"

    def _merge_context_files(
        self,
        parsed_context_files: object,
        repository_context: dict[str, object],
        skill_results: list[dict[str, object]],
    ) -> list[str]:
        merged: list[str] = []
        for item in list(parsed_context_files or []):
            text = str(item).strip()
            if text and text not in merged:
                merged.append(text)
        for item in list(repository_context.get("context_files", []) or []):
            text = str(item).strip()
            if text and text not in merged:
                merged.append(text)
        for result in skill_results:
            if str(result.get("skill_name") or "") != "repo_context_search":
                continue
            for item in list(result.get("context_files", []) or []):
                text = str(item).strip()
                if text and text not in merged:
                    merged.append(text)
        return merged[:6]

    def _build_debate_prompt(
        self,
        subject: ReviewSubject,
        issue: DebateIssue,
        expert: ExpertProfile,
        reply_to_expert_id: str,
        file_path: str,
        line_start: int,
    ) -> str:
        code_excerpt = self._build_code_excerpt(subject, file_path, line_start, expert.expert_id)
        return (
            f"议题标题: {issue.title}\n"
            f"议题摘要: {issue.summary}\n"
            f"当前专家: {expert.expert_id} / {expert.name_zh}\n"
            f"你要回应的对象: {reply_to_expert_id}\n"
            f"目标代码: {file_path}:{line_start}\n"
            f"职责边界: {' / '.join(expert.focus_areas) or expert.role}\n"
            f"禁止越界: {' / '.join(expert.out_of_scope) or '不要替其他专家下最终结论'}\n"
            f"代码片段:\n{code_excerpt}\n"
            f"请输出一段中文聊天式辩论消息，必须围绕 {file_path}:{line_start} 这段真实变更展开，"
            f"先点名回应对象，再说明你同意或反驳什么，指出具体代码证据，并说明还缺什么验证。"
        )

    def _build_debate_fallback(
        self,
        issue: DebateIssue,
        expert: ExpertProfile,
        reply_to_expert_id: str,
        file_path: str,
        line_start: int,
    ) -> str:
        return (
            f"回应 @{reply_to_expert_id}：我继续看了 {file_path}:{line_start}。"
            f" 对于“{issue.title}”这个议题，我认为争议点不只是 {issue.summary}，"
            f" 还要确认这里的边界条件和回退路径是否被覆盖，否则这个风险还不能直接关闭。"
        )

    def _extract_structured_field(self, text: str, label: str) -> str:
        marker = f"{label}："
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith(marker):
                return line.split(marker, 1)[1].strip()
        return ""

    def _parse_json_object(self, text: str) -> dict[str, object]:
        content = text.strip()
        candidates = [content]
        if "```json" in content:
            fragment = content.split("```json", 1)[1].split("```", 1)[0].strip()
            candidates.insert(0, fragment)
        if "```" in content and len(candidates) == 1:
            fragment = content.split("```", 1)[1].split("```", 1)[0].strip()
            candidates.insert(0, fragment)
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(content[start : end + 1])
            except Exception:
                return {}
            if isinstance(payload, dict):
                return payload
        return {}

    def _normalize_severity(self, value: object, fallback: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"blocker", "critical"}:
            return "blocker"
        if normalized in {"high", "medium", "low"}:
            return normalized
        return fallback

    def _normalize_confidence(self, value: object, fallback: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            return fallback
        return min(0.99, max(0.01, parsed))

    def _normalize_line_start(self, value: object, fallback: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return max(1, parsed)

    def _extract_summary(self, text: str, fallback: str) -> str:
        normalized = " ".join(text.replace("\n", " ").split())
        if not normalized:
            return fallback
        if len(normalized) <= 96:
            return normalized
        return normalized[:96].rstrip("，,。.;；:：") + "。"

    def _allow_llm_fallback(self, runtime_settings) -> bool:
        return bool(getattr(runtime_settings, "allow_llm_fallback", False) or os.getenv("PYTEST_CURRENT_TEST"))
