from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.report import ImpactReport
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMChatService, LLMTextResult

logger = logging.getLogger(__name__)


class ChangeImpactReportService:
    """按 Claude + GitNexus 的方式，把图谱事实翻译成 MR 级影响报告。"""

    WORKFLOW = [
        "先调用 GitNexus list_repos 确认仓库注册状态。",
        "再调用 detect_changes(scope=all) 识别本次 MR 的受影响对象和候选测试范围。",
        "随后围绕关键变更符号调用 context，补齐上下游调用、容器和流程语义。",
        "最后调用 impact 汇总 blast radius，再由 LLM 只基于这些事实生成可执行报告。",
    ]
    TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "builtin_experts" / "change_impact_analysis" / "report_template.md"
    DEFAULT_TEMPLATE_PATH = (
        Path(__file__).resolve().parents[1] / "builtin_experts" / "change_impact_analysis" / "report_template.default.md"
    )
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
    SCHEMA_PATH = (
        Path(__file__).resolve().parents[1] / "builtin_experts" / "change_impact_analysis" / "report_template.schema.json"
    )
    DEFAULT_SCHEMA_PATH = (
        Path(__file__).resolve().parents[1]
        / "builtin_experts"
        / "change_impact_analysis"
        / "report_template.schema.default.json"
    )

    def __init__(self) -> None:
        self._llm = LLMChatService()
        self._report_template = self._load_report_template()
        self._template_schema = self._load_template_schema()

    def synthesize(
        self,
        *,
        expert: ExpertProfile,
        runtime_settings: RuntimeSettings,
        report: ImpactReport,
        trace: dict[str, Any],
        review_id: str,
    ) -> tuple[ImpactReport, LLMTextResult | None]:
        report.analysis_workflow = list(self.WORKFLOW)
        report.fact_source = "gitnexus_mcp"
        resolution = self._llm.resolve_expert(expert, runtime_settings)
        fallback = self._fallback_payload(report)
        llm_result = self._llm.complete_text(
            system_prompt=self._system_prompt(expert),
            user_prompt=self._user_prompt(report, trace),
            resolution=resolution,
            runtime_settings=runtime_settings,
            fallback_text=json.dumps(fallback, ensure_ascii=False),
            allow_fallback=bool(runtime_settings.allow_llm_fallback),
            timeout_seconds=45.0,
            max_attempts=2,
            log_context={
                "review_id": review_id,
                "issue_id": "impact_report",
                "expert_id": expert.expert_id,
                "phase": "impact_analysis_report",
            },
        )
        payload = self._parse_json_payload(llm_result.text) or fallback
        summary = str(payload.get("summary") or fallback["summary"])
        key_impact_points = self._normalize_string_list(payload.get("key_impact_points")) or list(fallback["key_impact_points"])
        test_focus = self._normalize_string_list(payload.get("test_focus")) or list(fallback["test_focus"])
        manual_checks = self._normalize_string_list(payload.get("manual_checks")) or list(report.manual_verification)
        template_variables = self._normalize_template_variable_values(payload.get("template_variables"))
        markdown_candidate = str(payload.get("markdown") or "").strip()
        use_server_side_template = self._looks_like_intranet_template(self._report_template)
        if use_server_side_template:
            markdown = self._render_template(
                report=report,
                repo_name=str(trace.get("repo") or ""),
                source_branch=str(trace.get("source_branch") or ""),
                target_branch=str(trace.get("target_branch") or ""),
                queried_targets=[str(item) for item in list(trace.get("queried_targets") or [])[:12]],
                key_impact_points=key_impact_points,
                test_focus=test_focus,
                manual_checks=manual_checks,
                summary=summary,
                template_variables=template_variables,
            )
            markdown = self._normalize_intranet_markdown(markdown)
        elif markdown_candidate and not self._contains_unresolved_placeholders(markdown_candidate):
            markdown = markdown_candidate
        else:
            markdown = self._render_template(
                report=report,
                repo_name=str(trace.get("repo") or ""),
                source_branch=str(trace.get("source_branch") or ""),
                target_branch=str(trace.get("target_branch") or ""),
                queried_targets=[str(item) for item in list(trace.get("queried_targets") or [])[:12]],
                key_impact_points=key_impact_points,
                test_focus=test_focus,
                manual_checks=manual_checks,
                summary=summary,
                template_variables=template_variables,
            )
        if report.graph_status != "ready" or (
            report.queried_targets and not report.successful_context_targets and not report.successful_impact_targets
        ):
            summary = (
                "GitNexus 图谱事实不足，本次关联影响分析已降级为候选范围；"
                "请优先执行建议测试，并人工确认真实调用链。"
            )
            markdown = ""
        updated = report.model_copy(
            update={
                "report_summary": summary,
                "key_impact_points": key_impact_points,
                "test_focus": test_focus,
                "manual_verification": manual_checks,
                "llm_markdown": markdown,
                "llm_generated": llm_result.mode == "live",
            }
        )
        return updated, llm_result

    def _system_prompt(self, expert: ExpertProfile) -> str:
        prompt = str(expert.system_prompt or "").strip()
        if prompt:
            return (
                f"{prompt}\n\n"
                "你当前处于报告生成阶段，不需要再调用工具。"
                "GitNexus 的 detect_changes/context/impact 结果已经提供给你。"
                "你只能基于这些事实生成影响分析报告，不能猜测不存在的调用链。"
                "输出必须是 JSON。"
            )
        return (
            "你是关联性影响分析专家。"
            "你会收到 GitNexus MCP 的结构化事实，包括 detect_changes、context、impact 的结果。"
            "你的职责不是重新判断代码缺陷，而是把影响范围、关键链路和测试建议整理成研发和测试能直接执行的 MR 报告。"
            "不要虚构调用链、模块或测试建议；事实不够时要明确说未确认。"
            "输出必须是 JSON。"
        )

    def _user_prompt(self, report: ImpactReport, trace: dict[str, Any]) -> str:
        template_variables = self._schema_variables()
        facts = {
            "workflow": self.WORKFLOW,
            "repo": trace.get("repo") or "",
            "source_branch": trace.get("source_branch") or "",
            "target_branch": trace.get("target_branch") or "",
            "queried_targets": list(trace.get("queried_targets") or [])[:12],
            "available_repos": list(trace.get("available_repos") or [])[:12],
            "changed_files": report.changed_files,
            "changed_symbols": [item.model_dump(mode="json") for item in report.changed_symbols[:20]],
            "impacted_files": [item.model_dump(mode="json") for item in report.impacted_files[:24]],
            "impacted_modules": report.impacted_modules[:12],
            "impact_paths": [item.model_dump(mode="json") for item in report.impact_paths[:24]],
            "recommended_test_scope": [item.model_dump(mode="json") for item in report.recommended_test_scope[:16]],
            "manual_verification": report.manual_verification[:12],
            "detect_changes": trace.get("detect_changes") or {},
            "context_results": list(trace.get("context_results") or [])[:12],
            "impact_results": list(trace.get("impact_results") or [])[:12],
        }
        schema = {
            "summary": "一句话总结本次 MR 的核心影响范围和最优先测试项",
            "key_impact_points": ["3-6 条，讲清楚影响到了谁、为什么", "..."],
            "test_focus": ["3-6 条，按优先级给出该测什么", "..."],
            "manual_checks": ["需要人工确认的边界", "..."],
            "markdown": "严格按照模板最终生成的完整 Markdown 报告，所有占位符都已替换，遍历块都已展开",
            "template_variables": {
                item.get("name", "variable"): {
                    "description": item.get("description", "按模板变量要求输出"),
                    "source_hint": item.get("source", "llm"),
                    "format": item.get("format", "text"),
                }
                for item in template_variables
            },
        }
        return (
            "请严格参考 Claude + GitNexus 的工作方式：先尊重 GitNexus 图谱事实，再用人话输出影响分析。\n"
            "要求：\n"
            "1. 先讲本次 MR 改动影响了哪些入口、服务、仓储、流程或测试。\n"
            "2. 只引用 facts 里的对象，不要新增未出现的调用链。\n"
            "3. 测试建议要能直接执行，优先讲必须测什么，再讲建议补测什么。\n"
            "4. 如果某个影响只是候选关系，要明确说“候选”或“建议人工确认”。\n"
            "5. 最终必须输出一份完整的 markdown 字段，严格按照当前模板结构来呈现，包括表格、标题、列表和调用链块。\n"
            "6. 在“调用链影响分析”部分，优先使用 ```mermaid ... ``` 的 flowchart LR 展示调用链，每个变更方法至少一张图。\n"
            "7. 模板中的每一个变量都要由你结合 GitNexus facts 判断并填充，不要在最终 markdown 中保留任何 {{ }} 占位符。\n"
            "8. 如果模板中出现“遍历”“列出”“无则显示”这类说明，必须在 markdown 里展开成最终内容，而不是把说明原样抄回去。\n"
            "9. source_hint 只是参考，不是限制；最终值仍然由你基于 facts 生成，但不能虚构不存在的证据。\n"
            "10. 输出必须是 JSON，不要输出 Markdown 之外的解释。\n\n"
            f"Markdown 模板:\n{self._report_template}\n\n"
            f"模板变量 schema:\n{json.dumps(self._template_schema, ensure_ascii=False, indent=2)}\n\n"
            f"输出 schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            f"facts:\n{json.dumps(facts, ensure_ascii=False, indent=2)}"
        )

    def _fallback_payload(self, report: ImpactReport) -> dict[str, Any]:
        key_impact_points = [
            f"本次变更直接修改了 {len(report.changed_files)} 个文件，当前图谱识别出 {len(report.impacted_files)} 个候选受影响文件。"
        ]
        if report.impact_paths:
            key_impact_points.extend(
                [f"关键链路：{' -> '.join(item.path[:6])}" for item in report.impact_paths[:3] if item.path]
            )
        test_focus = [item.scope for item in report.recommended_test_scope[:6]]
        summary = (
            report.report_summary
            or f"本次 MR 已通过 GitNexus 分析出关联影响，建议优先回归 {', '.join(test_focus[:2]) or '关键变更链路'}。"
        )
        return {
            "summary": summary,
            "key_impact_points": key_impact_points[:6],
            "test_focus": test_focus[:6],
            "manual_checks": report.manual_verification[:6],
            "template_variables": {
                "summary": summary,
                "key_impact_points": self._bulletize(key_impact_points[:6]),
                "must_test": self._bulletize(test_focus[:2]),
                "should_test": self._bulletize(test_focus[2:6]),
                "manual_checks": self._bulletize(report.manual_verification[:6]),
            },
            "markdown": "",
        }

    def _parse_json_payload(self, text: str) -> dict[str, Any] | None:
        candidates = [str(text or "").strip()]
        candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", str(text or ""), flags=re.DOTALL | re.IGNORECASE))
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                payload = self._extract_json_object(candidate)
            if isinstance(payload, dict):
                return payload
        return None

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        logger.warning("change impact report json parse failed text=%s", text[:300])
                        return None
                    return payload if isinstance(payload, dict) else None
        return None

    def _normalize_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                items.append(text)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped[:12]

    def _normalize_template_variable_values(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, raw in value.items():
            name = str(key or "").strip()
            if not name:
                continue
            if isinstance(raw, list):
                text = self._bulletize([str(item or "").strip() for item in raw if str(item or "").strip()])
            else:
                text = str(raw or "").strip()
            if text:
                normalized[name] = text
        return normalized

    def _load_report_template(self) -> str:
        try:
            return self.TEMPLATE_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("change impact report template missing path=%s fallback=%s", self.TEMPLATE_PATH, self.DEFAULT_TEMPLATE_PATH)
            try:
                return self.DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
            except OSError:
                return (
                    "# 关联影响分析报告\n\n"
                    "## 1. 报告结论\n{{summary}}\n\n"
                    "## 2. 本次变更概览\n- 代码仓：{{repo_name}}\n- 变更文件数：{{changed_file_count}}\n\n"
                    "## 3. 关键关联影响\n{{key_impact_points}}\n\n"
                    "## 4. 关键调用链路\n{{impact_paths}}\n\n"
                    "## 5. 数据与事务影响\n### 5.1 数据访问层影响\n{{data_access_impact}}\n\n"
                    "### 5.2 事务边界影响\n{{transaction_impact}}\n\n"
                    "### 5.3 缓存 / 消息 / 异步任务影响\n{{integration_impact}}\n\n"
                    "## 6. 建议测试范围\n### 6.1 必须优先测试\n{{must_test}}\n\n"
                    "### 6.2 建议补充测试\n{{should_test}}\n\n"
                    "### 6.3 建议人工确认\n{{manual_checks}}\n\n"
                    "## 7. 使用边界\n{{limitations}}"
                )

    def _load_template_schema(self) -> dict[str, Any]:
        for path in (self.SCHEMA_PATH, self.DEFAULT_SCHEMA_PATH):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            except json.JSONDecodeError:
                logger.warning("change impact report schema invalid path=%s", path)
                continue
            if isinstance(payload, dict):
                return payload
        return {
            "variables": [
                {"name": "summary", "source": "llm", "required": True, "description": "一句话结论"},
                {"name": "key_impact_points", "source": "llm", "required": False, "description": "关键影响点"},
                {"name": "must_test", "source": "llm", "required": False, "description": "必须优先测试项"},
                {"name": "manual_checks", "source": "llm", "required": False, "description": "人工确认项"},
            ]
        }

    def _schema_variables(self) -> list[dict[str, Any]]:
        variables = self._template_schema.get("variables")
        if not isinstance(variables, list):
            return []
        return [item for item in variables if isinstance(item, dict) and str(item.get("name") or "").strip()]

    def analyze_template_schema(self, template_content: str, runtime_settings: RuntimeSettings) -> dict[str, Any]:
        template = str(template_content or "").strip()
        placeholders = self.extract_template_placeholders(template)
        fallback_schema = self._fallback_template_schema(placeholders)
        if not placeholders:
            return fallback_schema
        resolution = self._llm.resolve_main_agent(runtime_settings)
        llm_result = self._llm.complete_text(
            system_prompt=(
                "你是代码审核系统的模板变量分析助手。"
                "你的职责是分析 Markdown 模板里的占位符应该如何定义。"
                "输出必须是 JSON。"
            ),
            user_prompt=(
                "请基于下面的 Markdown 模板分析变量定义。\n"
                "要求：\n"
                "1. 逐个占位符给出 name、source、required、description、format。\n"
                "2. source 只能是 system、gitnexus、llm 三种。\n"
                "3. 已知固定字段如 repo_name、changed_files、impact_paths、risk_level 优先归到 system 或 gitnexus。\n"
                "4. 需要总结、解释、建议、人工判断的字段归到 llm。\n"
                "5. 输出 JSON 格式：{\"variables\":[...]}\n\n"
                f"占位符：{json.dumps(placeholders, ensure_ascii=False)}\n\n"
                f"模板内容：\n{template}"
            ),
            resolution=resolution,
            runtime_settings=runtime_settings,
            fallback_text=json.dumps(fallback_schema, ensure_ascii=False),
            allow_fallback=True,
            timeout_seconds=30.0,
            max_attempts=2,
            log_context={"phase": "impact_template_schema_analysis"},
        )
        payload = self._parse_json_payload(llm_result.text)
        normalized = self._normalize_template_schema_payload(payload, placeholders)
        return normalized or fallback_schema

    def render_preview(self, template_content: str, schema_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        preview_service = ChangeImpactReportService()
        if str(template_content or "").strip():
            preview_service._report_template = str(template_content).strip()
        if isinstance(schema_payload, dict) and schema_payload:
            preview_service._template_schema = schema_payload
        report = self._build_preview_report()
        template_variables = preview_service._build_preview_llm_variables()
        markdown = preview_service._render_template(
            report=report,
            repo_name="order-service",
            source_branch="feature/order-impact",
            target_branch="main",
            queried_targets=["OrderController.createOrder", "OrderApplicationService.createOrder", "OrderRepository.save"],
            key_impact_points=[
                "订单创建入口和仓储保存链路被同时波及，需要把接口、事务和写库顺序作为同一个测试单元来验证。",
                "图谱识别到 OrderRepository.save 位于主调用链下游，数据访问层属于高风险影响点。",
                "audit-topic 出现在外部触点中，说明审计消息链路也需要一起确认。",
            ],
            test_focus=[
                "必须先回归订单创建接口，覆盖正常创建、重复下单和异常回滚路径。",
                "必须验证订单落库与审计消息发送的先后顺序，确认事务提交前后没有顺序倒置。",
                "建议补测 OrderRepository 相关数据访问测试，关注 SQL 条件和批量写入行为。",
            ],
            manual_checks=[
                "确认审计消费者是否依赖新增字段或旧字段顺序。",
                "确认灰度开关开启时是否会走到另一条异步发送路径。",
            ],
            summary="本次改动主要影响订单创建主链路，优先回归接口、事务边界和仓储写入逻辑。",
            template_variables=template_variables,
        )
        return {
            "markdown": markdown,
            "placeholders": self.extract_template_placeholders(preview_service._report_template),
            "schema_variables": preview_service._schema_variables(),
        }

    def _fallback_template_schema(self, placeholders: list[str]) -> dict[str, Any]:
        return {
            "variables": [self._fallback_variable_definition(name) for name in placeholders],
        }

    def _fallback_variable_definition(self, name: str) -> dict[str, Any]:
        system_fields = {"repo_name", "changed_file_count", "changed_files", "fact_source", "analysis_workflow", "limitations"}
        gitnexus_fields = {
            "changed_symbols",
            "impact_paths",
            "impacted_files",
            "impacted_modules",
            "external_entrypoints",
            "risk_level",
            "queried_targets",
        }
        llm_fields = {
            "summary",
            "key_impact_points",
            "must_test",
            "should_test",
            "manual_checks",
            "data_access_impact",
            "transaction_impact",
            "integration_impact",
        }
        source = "llm"
        if name in system_fields:
            source = "system"
        elif name in gitnexus_fields:
            source = "gitnexus"
        elif name in llm_fields:
            source = "llm"
        format_hint = "bullet_list" if any(token in name for token in ["files", "symbols", "points", "paths", "modules", "checks", "test"]) else "text"
        return {
            "name": name,
            "source": source,
            "required": name in {"summary", "repo_name"},
            "description": f"模板变量 {name}",
            "format": format_hint,
        }

    def _normalize_template_schema_payload(self, payload: dict[str, Any] | None, placeholders: list[str]) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        variables = payload.get("variables")
        if not isinstance(variables, list):
            return None
        by_name: dict[str, dict[str, Any]] = {}
        for item in variables:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            source = str(item.get("source") or "llm").strip().lower()
            if source not in {"system", "gitnexus", "llm"}:
                source = "llm"
            by_name[name] = {
                "name": name,
                "source": source,
                "required": bool(item.get("required")),
                "description": str(item.get("description") or f"模板变量 {name}").strip(),
                "format": str(item.get("format") or "text").strip() or "text",
            }
        if not by_name:
            return None
        return {
            "variables": [by_name.get(name) or self._fallback_variable_definition(name) for name in placeholders],
        }

    @classmethod
    def extract_template_placeholders(cls, content: str) -> list[str]:
        matches = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", str(content or ""))
        deduped: list[str] = []
        seen: set[str] = set()
        for item in matches:
            name = str(item or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped

    def _contains_unresolved_placeholders(self, markdown: str) -> bool:
        return bool(re.search(r"\{\{\s*[^{}]+?\s*\}\}", str(markdown or "")))

    def _render_template(
        self,
        *,
        report: ImpactReport,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        queried_targets: list[str],
        key_impact_points: list[str],
        test_focus: list[str],
        manual_checks: list[str],
        summary: str,
        template_variables: dict[str, str] | None = None,
    ) -> str:
        if self._looks_like_intranet_template(self._report_template):
            return self._render_intranet_template(
                report=report,
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                queried_targets=queried_targets,
                key_impact_points=key_impact_points,
                test_focus=test_focus,
                manual_checks=manual_checks,
                summary=summary,
                template_variables=template_variables,
            )
        must_test = test_focus[:2]
        should_test = test_focus[2:] or [item.scope for item in report.recommended_test_scope[2:6]]
        data_access_impact = self._derive_data_access_impact(report)
        transaction_impact = self._derive_transaction_impact(report)
        integration_impact = self._derive_integration_impact(report)
        base_context = {
            "summary": summary or "暂无总结",
            "repo_name": repo_name or "未知仓库",
            "source_branch": source_branch or "未知源分支",
            "target_branch": target_branch or "未知目标分支",
            "changed_file_count": str(len(report.changed_files)),
            "changed_files": self._bulletize(report.changed_files),
            "changed_symbols": self._bulletize(
                [
                    f"{item.container + '.' if item.container else ''}{item.symbol} ({item.file_path})"
                    for item in report.changed_symbols[:12]
                ]
            ),
            "fact_source": report.fact_source or "gitnexus_mcp",
            "key_impact_points": self._bulletize(key_impact_points),
            "impact_paths": self._bulletize(
                [
                    " -> ".join(item.path) if item.path else f"{item.source} -> {item.target}"
                    for item in report.impact_paths[:10]
                ]
            ),
            "data_access_impact": self._bulletize(data_access_impact),
            "transaction_impact": self._bulletize(transaction_impact),
            "integration_impact": self._bulletize(integration_impact),
            "impacted_files": self._bulletize(
                [f"{item.file_path} | {item.relationship} | {item.reason}" for item in report.impacted_files[:16]]
            ),
            "impacted_modules": self._bulletize(report.impacted_modules),
            "external_entrypoints": self._bulletize(report.external_entrypoints),
            "must_test": self._bulletize(must_test),
            "should_test": self._bulletize(should_test),
            "manual_checks": self._bulletize(manual_checks),
            "analysis_workflow": self._bulletize(report.analysis_workflow or self.WORKFLOW),
            "queried_targets": self._bulletize(queried_targets),
            "risk_level": self._bulletize([report.risk_level or "unknown"]),
            "limitations": self._bulletize(report.limitations),
        }
        merged_context = dict(base_context)
        merged_context.update({key: value for key, value in dict(template_variables or {}).items() if str(value or "").strip()})
        return re.sub(
            r"\{\{\s*([^{}]+?)\s*\}\}",
            lambda match: str(merged_context.get(str(match.group(1) or "").strip()) or "- 暂无"),
            self._report_template,
        )

    def _looks_like_intranet_template(self, template: str) -> bool:
        markers = (
            "遍历 java_methods，每行一条记录",
            "遍历 mybatis_sqls，每行一条记录",
            "遍历 call_chains，每个方法生成以下块",
            "列出高风险项：如修改了被多处调用的核心方法",
        )
        return any(marker in str(template or "") for marker in markers)

    def _render_intranet_template(
        self,
        *,
        report: ImpactReport,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        queried_targets: list[str],
        key_impact_points: list[str],
        test_focus: list[str],
        manual_checks: list[str],
        summary: str,
        template_variables: dict[str, str] | None = None,
    ) -> str:
        rendered = str(self._report_template or "")
        now_text = datetime.now(self.SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
        java_method_rows = self._build_java_method_rows(report)
        mybatis_rows = self._build_mybatis_sql_rows(report)
        call_chain_blocks = self._build_call_chain_blocks(report)
        risk_items = self._build_risk_items(report, key_impact_points)
        test_scenarios = self._build_test_scenarios(report, test_focus)
        exception_lines = self._build_analysis_exceptions(report, manual_checks)
        manual_section = self._bulletize(manual_checks) if manual_checks else "无异常"
        base_context = {
            "当前时间": now_text,
            "source_branch": source_branch or repo_name or "未知源分支",
            "target_branch": target_branch or "未知目标分支",
            "java_file_count": str(sum(1 for path in report.changed_files if path.endswith(".java"))),
            "xml_file_count": str(sum(1 for path in report.changed_files if path.endswith(".xml"))),
            "method_count": str(len(report.changed_symbols)),
            "sql_count": str(len(self._collect_mybatis_sql_entries(report))),
            "summary": summary or "暂无总结",
            "repo_name": repo_name or "未知仓库",
            "queried_targets": self._bulletize(queried_targets),
            "manual_checks": manual_section,
            "根据调用链和变更类型，列出需要重点测试的场景": test_scenarios,
            "测试场景 1": test_focus[0] if test_focus else "变更文件对应的单元测试",
            "测试场景 2": test_focus[1] if len(test_focus) > 1 else "接口级回归测试",
            "如果 errors 数组非空，列出查询失败的方法": exception_lines if exception_lines != "无异常" else "无异常",
            "如果无异常，显示“无异常”": "无异常" if exception_lines == "无异常" else "",
            "列出高风险项：如修改了被多处调用的核心方法": risk_items["high"],
            "列出中风险项：如修改了有上下游依赖的方法": risk_items["medium"],
            "列出低风险项：如新增方法、无调用链影响": risk_items["low"],
        }
        merged_context = dict(base_context)
        merged_context.update({key: value for key, value in dict(template_variables or {}).items() if str(value or "").strip()})
        rendered = re.sub(r"\{\{\s*遍历 java_methods，每行一条记录\s*\}\}", java_method_rows, rendered)
        rendered = re.sub(r"\{\{\s*遍历 mybatis_sqls，每行一条记录\s*\}\}", mybatis_rows, rendered)
        rendered = self._render_call_chain_loop(rendered, call_chain_blocks)
        rendered = re.sub(
            r"\{\{\s*([^{}]+?)\s*\}\}",
            lambda match: str(merged_context.get(str(match.group(1) or "").strip()) or "- 暂无"),
            rendered,
        )
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        if self._contains_unresolved_placeholders(rendered):
            rendered = self._strip_remaining_placeholders(rendered)
        return rendered

    def _render_call_chain_loop(self, template: str, blocks: list[dict[str, str]]) -> str:
        pattern = re.compile(
            r"\{\{\s*遍历 call_chains，每个方法生成以下块\s*\}\}(.*?)\{\{\s*结束遍历\s*\}\}",
            flags=re.DOTALL,
        )
        match = pattern.search(template)
        if not match:
            return template
        loop_body = str(match.group(1) or "").strip("\n")
        rendered_blocks: list[str] = []
        payloads = blocks or [self._empty_call_chain_block()]
        for block in payloads:
            rendered_block = re.sub(
                    r"\{\{\s*([^{}]+?)\s*\}\}",
                    lambda item: str(block.get(str(item.group(1) or "").strip()) or "- 暂无"),
                    loop_body,
                ).strip()
            mermaid_chart = str(block.get("mermaid_chart") or "").strip()
            if mermaid_chart and "```mermaid" not in rendered_block:
                rendered_block = self._inject_mermaid_after_heading(rendered_block, mermaid_chart)
            rendered_blocks.append(rendered_block)
        return template[: match.start()] + "\n\n".join(rendered_blocks) + template[match.end() :]

    def _inject_mermaid_after_heading(self, block: str, mermaid_chart: str) -> str:
        lines = str(block or "").splitlines()
        if not lines:
            return f"{mermaid_chart}\n{block}".strip()
        return "\n".join([lines[0], "", mermaid_chart, "", *lines[1:]]).strip()

    def _build_java_method_rows(self, report: ImpactReport) -> str:
        rows: list[str] = []
        changed_files = {path for path in report.changed_files}
        for item in report.changed_symbols[:24]:
            change_type = "modified" if item.file_path in changed_files else "impacted"
            rows.append(
                f"| {item.container or '未知类'} | {item.symbol or '未知方法'} | {change_type} | {item.file_path or '未知文件'} |"
            )
        return "\n".join(rows) if rows else "| - 暂无 | - 暂无 | - 暂无 | - 暂无 |"

    def _collect_mybatis_sql_entries(self, report: ImpactReport) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for path in report.changed_files:
            lowered = path.lower()
            if not lowered.endswith(".xml") or "mapper" not in lowered and "mybatis" not in lowered:
                continue
            namespace = Path(path).stem or "未知Namespace"
            entries.append(
                {
                    "namespace": namespace,
                    "sql_id": "待人工确认",
                    "sql_type": "MyBatis XML",
                    "change_type": "modified",
                    "file_path": path,
                }
            )
        return entries

    def _build_mybatis_sql_rows(self, report: ImpactReport) -> str:
        rows = [
            f"| {item['namespace']} | {item['sql_id']} | {item['sql_type']} | {item['change_type']} | {item['file_path']} |"
            for item in self._collect_mybatis_sql_entries(report)[:12]
        ]
        return "\n".join(rows) if rows else "| 无 | 无 | 无 | 无 | 无 |"

    def _build_call_chain_blocks(self, report: ImpactReport) -> list[dict[str, str]]:
        blocks: list[dict[str, str]] = []
        for symbol in report.changed_symbols[:12]:
            method_signature = f"{symbol.container + '.' if symbol.container else ''}{symbol.symbol}".strip(".") or "未知方法"
            upstream = self._dedupe(
                [
                    " -> ".join(path.path)
                    for path in report.impact_paths
                    if path.path and method_signature in path.path[1:]
                ]
            )
            downstream = self._dedupe(
                [
                    " -> ".join(path.path)
                    for path in report.impact_paths
                    if path.path and (
                        method_signature == path.path[0]
                        or symbol.symbol in path.path
                        or (symbol.container and symbol.container in path.path)
                    )
                ]
            )
            related_files = [item.file_path for item in report.impacted_files if item.file_path and symbol.file_path != item.file_path]
            impact_summary_parts = []
            if downstream:
                impact_summary_parts.append(f"该方法所在链路会继续触达 {', '.join(downstream[:2])}。")
            if upstream:
                impact_summary_parts.append(f"上游已有调用方依赖这段逻辑，需要联动验证 {', '.join(upstream[:2])}。")
            if related_files:
                impact_summary_parts.append(f"当前候选受影响文件包括 {', '.join(related_files[:2])}。")
            if not impact_summary_parts:
                impact_summary_parts.append("当前图谱未给出更深调用链，建议结合代码上下文人工确认该方法的上下游依赖。")
            blocks.append(
                {
                    "method_signature": method_signature,
                    "mermaid_chart": self._build_call_chain_mermaid(
                        method_signature=method_signature,
                        upstream=upstream,
                        downstream=downstream,
                        impacted_files=related_files,
                        recommended_tests=[item.scope for item in report.recommended_test_scope[:2]],
                    ),
                    "列出 callers，无则显示 “无上游调用”": self._bulletize(upstream) if upstream else "无上游调用",
                    "列出 callees，无则显示 “无下游调用”": self._bulletize(downstream) if downstream else "无下游调用",
                    "基于调用链分析，简要说明该方法变更可能带来的影响": " ".join(impact_summary_parts),
                }
            )
        return blocks

    def _empty_call_chain_block(self) -> dict[str, str]:
        return {
            "method_signature": "暂无可展开的方法",
            "mermaid_chart": "```mermaid\nflowchart LR\n    start[\"暂无可展开的方法\"]\n```",
            "列出 callers，无则显示 “无上游调用”": "无上游调用",
            "列出 callees，无则显示 “无下游调用”": "无下游调用",
            "基于调用链分析，简要说明该方法变更可能带来的影响": "当前图谱没有返回可展开的调用链，请结合实际代码人工确认。",
        }

    def _build_call_chain_mermaid(
        self,
        *,
        method_signature: str,
        upstream: list[str],
        downstream: list[str],
        impacted_files: list[str],
        recommended_tests: list[str],
    ) -> str:
        lines = [
            "```mermaid",
            "flowchart LR",
            "    classDef changed fill:#dbeafe,stroke:#2563eb,color:#0f172a,stroke-width:2px;",
            "    classDef downstream fill:#eef2ff,stroke:#6366f1,color:#1e1b4b;",
            "    classDef file fill:#fef3c7,stroke:#d97706,color:#78350f;",
            "    classDef test fill:#dcfce7,stroke:#16a34a,color:#14532d;",
        ]
        center_id = self._mermaid_node_id(f"center::{method_signature}")
        lines.append(f'    {center_id}["{self._escape_mermaid_label(method_signature)}"]')
        lines.append(f"    class {center_id} changed")
        for index, item in enumerate(upstream[:3], start=1):
            node_id = self._mermaid_node_id(f"up::{method_signature}::{index}")
            lines.append(f'    {node_id}["{self._escape_mermaid_label(self._compact_chain_label(item))}"]')
            lines.append(f"    {node_id} --> {center_id}")
            lines.append(f"    class {node_id} downstream")
        for index, item in enumerate(downstream[:4], start=1):
            node_id = self._mermaid_node_id(f"down::{method_signature}::{index}")
            lines.append(f'    {node_id}["{self._escape_mermaid_label(self._compact_chain_label(item))}"]')
            lines.append(f"    {center_id} --> {node_id}")
            lines.append(f"    class {node_id} downstream")
        for index, item in enumerate(impacted_files[:2], start=1):
            node_id = self._mermaid_node_id(f"file::{method_signature}::{index}")
            file_name = Path(item).name or item
            lines.append(f'    {node_id}["文件: {self._escape_mermaid_label(file_name)}"]')
            lines.append(f"    {center_id} -.-> {node_id}")
            lines.append(f"    class {node_id} file")
        for index, item in enumerate(recommended_tests[:2], start=1):
            node_id = self._mermaid_node_id(f"test::{method_signature}::{index}")
            lines.append(f'    {node_id}["测试: {self._escape_mermaid_label(item)}"]')
            lines.append(f"    {center_id} -.-> {node_id}")
            lines.append(f"    class {node_id} test")
        lines.append("```")
        return "\n".join(lines)

    def _mermaid_node_id(self, value: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", str(value or "node"))
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        return sanitized or "node"

    def _escape_mermaid_label(self, value: str) -> str:
        text = str(value or "").replace('"', '\\"')
        return text[:120]

    def _compact_chain_label(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "->" not in text:
            return self._short_symbol_name(text)
        parts = [self._short_symbol_name(item) for item in text.split("->")]
        compact = " -> ".join(item for item in parts if item)
        return compact[:72] if compact else text[:72]

    def _short_symbol_name(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = Path(text).name or text
        text = text.replace(".java", "")
        if "." in text and " " not in text:
            segments = [segment for segment in text.split(".") if segment]
            if len(segments) >= 2:
                text = ".".join(segments[-2:])
            elif segments:
                text = segments[-1]
        return text[:32]

    def _build_risk_items(self, report: ImpactReport, key_impact_points: list[str]) -> dict[str, str]:
        high: list[str] = []
        medium: list[str] = []
        low: list[str] = []
        for symbol in report.changed_symbols:
            signature = f"{symbol.container + '.' if symbol.container else ''}{symbol.symbol}".strip(".")
            related = [path for path in report.impact_paths if signature in path.path or symbol.symbol in path.path]
            if len(related) >= 2 or report.risk_level.lower() == "high":
                high.append(f"{signature} 关联到 {len(related) or len(report.impact_paths)} 条调用链，属于核心变更点。")
            elif related:
                medium.append(f"{signature} 存在上下游依赖，建议联动验证调用方与下游实现。")
            else:
                low.append(f"{signature} 当前未识别出明显扩散链路，更多影响需结合代码人工确认。")
        if not high and report.risk_level.lower() == "medium":
            medium.extend(key_impact_points[:2])
        if not low:
            low.append("当前未识别出纯新增且无依赖扩散的方法。")
        return {
            "high": "；".join(self._dedupe(high)[:3]) or "无高风险项",
            "medium": "；".join(self._dedupe(medium)[:3]) or "无中风险项",
            "low": "；".join(self._dedupe(low)[:3]) or "无低风险项",
        }

    def _build_test_scenarios(self, report: ImpactReport, test_focus: list[str]) -> str:
        lines: list[str] = []
        for index, item in enumerate(test_focus[:5], start=1):
            lines.append(f"{index}. {item}")
        next_index = len(lines) + 1
        for scope in report.recommended_test_scope[:5]:
            candidate = f"{scope.scope}：{scope.reason}"
            if any(scope.scope in line for line in lines):
                continue
            lines.append(f"{next_index}. {candidate}")
            next_index += 1
        return "\n".join(lines) if lines else "1. 变更文件对应的单元测试\n2. 接口级回归测试"

    def _build_analysis_exceptions(self, report: ImpactReport, manual_checks: list[str]) -> str:
        exception_lines = [item for item in report.limitations if "失败" in item or "异常" in item or "未" in item]
        if exception_lines:
            return self._bulletize(exception_lines[:6])
        if manual_checks:
            return "无异常"
        return "无异常"

    def _strip_remaining_placeholders(self, text: str) -> str:
        cleaned = re.sub(r"\{\{\s*[^{}]+?\s*\}\}", "- 暂无", text)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _normalize_intranet_markdown(self, markdown: str) -> str:
        lines = [line.rstrip() for line in str(markdown or "").splitlines()]
        normalized: list[str] = []
        seen_numbered: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if re.fullmatch(r"\d+\.\s*\.\.\.", stripped):
                continue
            if normalized and stripped == normalized[-1].strip() and stripped in {"无异常", "---"}:
                continue
            if re.match(r"^\d+\.\s+", stripped):
                if stripped in seen_numbered:
                    continue
                seen_numbered.add(stripped)
            normalized.append(line)
        text = "\n".join(normalized)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _bulletize(self, items: list[str]) -> str:
        normalized = [str(item or "").strip() for item in items if str(item or "").strip()]
        if not normalized:
            return "- 暂无"
        return "\n".join(f"- {item}" for item in normalized)

    def _derive_data_access_impact(self, report: ImpactReport) -> list[str]:
        hits: list[str] = []
        for item in report.changed_files + [entry.file_path for entry in report.impacted_files]:
            path = item.lower()
            if any(token in path for token in ("repository", "mapper", "dao", ".sql", "mybatis", "jpa")):
                hits.append(f"涉及数据访问对象或 SQL：{item}")
        for item in report.impact_paths[:8]:
            joined = " ".join(item.path).lower()
            if any(token in joined for token in ("repository", "mapper", "dao", "sql")):
                hits.append(f"调用链触达数据访问层：{' -> '.join(item.path[:6])}")
        deduped = self._dedupe(hits)
        if deduped:
            return deduped[:6]
        return ["当前图谱未明确指向 Repository / Mapper / SQL 变更，请结合实际代码确认是否涉及数据访问层。"]

    def _derive_transaction_impact(self, report: ImpactReport) -> list[str]:
        hits: list[str] = []
        for item in report.changed_symbols[:12]:
            symbol_text = f"{item.container}.{item.symbol}".lower() if item.container else item.symbol.lower()
            if any(token in symbol_text for token in ("create", "save", "submit", "confirm", "commit", "pay", "order")):
                hits.append(f"关键变更符号可能位于事务主链路：{item.container + '.' if item.container else ''}{item.symbol}")
        for path in report.impact_paths[:8]:
            joined = " ".join(path.path).lower()
            if any(token in joined for token in ("service", "repository", "publisher", "event", "transaction")):
                hits.append(f"需要确认调用链中的事务边界和提交顺序：{' -> '.join(path.path[:6])}")
        deduped = self._dedupe(hits)
        if deduped:
            return deduped[:6]
        return ["当前图谱未明确暴露事务边界变化，请重点人工确认写库、发消息、发事件的先后顺序。"]

    def _derive_integration_impact(self, report: ImpactReport) -> list[str]:
        hits: list[str] = []
        haystacks = report.changed_files + report.external_entrypoints + [entry.file_path for entry in report.impacted_files]
        for item in haystacks:
            lowered = item.lower()
            if any(token in lowered for token in ("mq", "kafka", "rocketmq", "redis", "cache", "listener", "consumer", "job", "schedule", "feign", "client")):
                hits.append(f"涉及外部集成或异步链路：{item}")
        for path in report.impact_paths[:8]:
            joined = " ".join(path.path).lower()
            if any(token in joined for token in ("publisher", "consumer", "listener", "redis", "cache", "mq", "job", "schedule")):
                hits.append(f"调用链波及缓存、消息或异步处理：{' -> '.join(path.path[:6])}")
        deduped = self._dedupe(hits)
        if deduped:
            return deduped[:6]
        return ["当前图谱未明确指向缓存、消息或异步任务，请结合 MQ / Redis / 定时任务链路人工确认。"]

    def _dedupe(self, items: list[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            results.append(text)
        return results

    def _build_preview_llm_variables(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for item in self._schema_variables():
            name = str(item.get("name") or "").strip()
            if not name or item.get("source") != "llm":
                continue
            description = str(item.get("description") or name).strip()
            if name == "summary":
                values[name] = "本次改动主要影响订单创建主链路，建议优先回归接口、事务边界和数据访问层。"
            elif name == "key_impact_points":
                values[name] = self._bulletize(
                    [
                        "订单入口、应用服务和仓储写入链路被同时波及。",
                        "图谱识别出仓储保存和审计消息链路都属于高优先级确认对象。",
                    ]
                )
            elif name == "must_test":
                values[name] = self._bulletize(
                    [
                        "订单创建接口集成测试",
                        "订单落库与审计消息顺序验证",
                    ]
                )
            elif name == "should_test":
                values[name] = self._bulletize(
                    [
                        "OrderRepository 数据访问测试",
                        "异常回滚与重复下单场景",
                    ]
                )
            elif name == "manual_checks":
                values[name] = self._bulletize(
                    [
                        "确认消息消费者是否依赖旧字段结构",
                        "确认灰度配置是否会切换到另一条异步链路",
                    ]
                )
            else:
                values[name] = f"示例内容：{description}"
        return values

    def _build_preview_report(self) -> ImpactReport:
        return ImpactReport(
            graph_status="ready",
            graph_commit="preview-commit",
            fact_source="gitnexus_mcp",
            analysis_workflow=list(self.WORKFLOW),
            changed_files=[
                "src/main/java/com/example/order/OrderController.java",
                "src/main/java/com/example/order/OrderApplicationService.java",
                "src/main/java/com/example/order/OrderRepository.java",
            ],
            changed_symbols=[
                {"file_path": "src/main/java/com/example/order/OrderController.java", "symbol": "createOrder", "kind": "function", "container": "OrderController", "line_start": 32},
                {"file_path": "src/main/java/com/example/order/OrderApplicationService.java", "symbol": "createOrder", "kind": "function", "container": "OrderApplicationService", "line_start": 54},
                {"file_path": "src/main/java/com/example/order/OrderRepository.java", "symbol": "save", "kind": "function", "container": "OrderRepository", "line_start": 18},
            ],
            impacted_files=[
                {"file_path": "src/main/java/com/example/order/OrderRepository.java", "relationship": "data_access", "reason": "订单保存逻辑位于主调用链下游", "risk_level": "high"},
                {"file_path": "src/test/java/com/example/order/OrderControllerTest.java", "relationship": "test_candidate", "reason": "订单创建入口测试需要优先回归", "risk_level": "medium"},
            ],
            impacted_modules=["order", "audit"],
            impact_paths=[
                {"source": "OrderController.createOrder", "target": "OrderRepository.save", "path": ["OrderController.createOrder", "OrderApplicationService.createOrder", "OrderRepository.save"], "depth": 2, "risk": "high"},
                {"source": "OrderApplicationService.createOrder", "target": "audit-topic", "path": ["OrderApplicationService.createOrder", "AuditPublisher.publish", "audit-topic"], "depth": 2, "risk": "medium"},
            ],
            external_entrypoints=["order-api", "audit-topic"],
            risk_level="high",
            recommended_test_scope=[
                {"scope": "订单创建接口集成测试", "reason": "入口层和应用服务链路同时变更", "paths": ["src/test/java/com/example/order/OrderControllerTest.java"], "priority": "high"},
                {"scope": "数据访问与事务顺序验证", "reason": "仓储写入和消息发送链路同时受影响", "paths": ["OrderRepository.save", "AuditPublisher.publish"], "priority": "high"},
                {"scope": "异常回滚与幂等场景回归", "reason": "需要确认失败路径是否保持一致", "paths": ["OrderApplicationService.createOrder"], "priority": "medium"},
            ],
            manual_verification=[
                "确认审计消息消费者是否依赖旧字段结构。",
                "确认灰度配置是否会切换到另一条异步链路。",
            ],
            limitations=["这是模板预览示例数据，用于验证模板结构和占位符渲染效果。"],
        )
