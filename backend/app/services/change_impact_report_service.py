from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

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
            allow_fallback=True,
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
        markdown = self._render_template(
            report=report,
            repo_name=str(trace.get("repo") or ""),
            queried_targets=[str(item) for item in list(trace.get("queried_targets") or [])[:12]],
            key_impact_points=key_impact_points,
            test_focus=test_focus,
            manual_checks=manual_checks,
            summary=summary,
            template_variables=template_variables,
        )
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
        llm_variables = [item for item in self._schema_variables() if item.get("source") == "llm"]
        facts = {
            "workflow": self.WORKFLOW,
            "repo": trace.get("repo") or "",
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
            "template_variables": {
                item.get("name", "variable"): item.get("description", "按模板变量要求输出")
                for item in llm_variables
            },
        }
        return (
            "请严格参考 Claude + GitNexus 的工作方式：先尊重 GitNexus 图谱事实，再用人话输出影响分析。\n"
            "要求：\n"
            "1. 先讲本次 MR 改动影响了哪些入口、服务、仓储、流程或测试。\n"
            "2. 只引用 facts 里的对象，不要新增未出现的调用链。\n"
            "3. 测试建议要能直接执行，优先讲必须测什么，再讲建议补测什么。\n"
            "4. 如果某个影响只是候选关系，要明确说“候选”或“建议人工确认”。\n"
            "5. 只为 source=llm 的模板变量生成内容，不能覆盖 system/gitnexus 已经提供的事实字段。\n"
            "6. 输出必须是 JSON，不要输出 Markdown 之外的解释。\n\n"
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
        markdown_lines = [
            self._render_template(
                report=report,
                repo_name="当前仓库",
                queried_targets=[],
                key_impact_points=key_impact_points[:6],
                test_focus=test_focus[:6],
                manual_checks=report.manual_verification[:6],
                summary=summary,
            )
        ]
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
            "markdown": "\n".join(markdown_lines),
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
        matches = re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", str(content or ""))
        deduped: list[str] = []
        seen: set[str] = set()
        for item in matches:
            name = str(item or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped

    def _render_template(
        self,
        *,
        report: ImpactReport,
        repo_name: str,
        queried_targets: list[str],
        key_impact_points: list[str],
        test_focus: list[str],
        manual_checks: list[str],
        summary: str,
        template_variables: dict[str, str] | None = None,
    ) -> str:
        must_test = test_focus[:2]
        should_test = test_focus[2:] or [item.scope for item in report.recommended_test_scope[2:6]]
        data_access_impact = self._derive_data_access_impact(report)
        transaction_impact = self._derive_transaction_impact(report)
        integration_impact = self._derive_integration_impact(report)
        base_context = {
            "summary": summary or "暂无总结",
            "repo_name": repo_name or "未知仓库",
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
        rendered = self._report_template
        for placeholder in self.extract_template_placeholders(self._report_template):
            rendered = rendered.replace(f"{{{{{placeholder}}}}}", str(merged_context.get(placeholder) or "- 暂无"))
        return rendered

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
