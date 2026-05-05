from __future__ import annotations

import os
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def stub_required_llm_live_calls(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep review-flow tests on the mandatory LLM path without requiring live API keys."""

    test_path = str(getattr(request.node, "path", "") or "")
    if test_path.endswith("test_llm_runtime_defaults.py") or test_path.endswith("test_llm_chat_service_timeouts.py"):
        return

    from app.services.llm_chat_service import LLMTextResult

    def _result(text: str, phase: str, *, resolution=None) -> LLMTextResult:
        return LLMTextResult(
            text=text,
            mode="live",
            provider=getattr(resolution, "provider", None) or "test",
            model=getattr(resolution, "model", None) or f"test-{phase or 'llm'}",
            base_url=getattr(resolution, "base_url", None) or "http://llm.test",
            api_key_env=getattr(resolution, "api_key_env", None) or "TEST_KEY",
            call_id=f"test-{phase or 'llm'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    def _fake_complete_text(
        _self,
        *,
        system_prompt: str,
        user_prompt: str,
        resolution,
        runtime_settings=None,
        fallback_text: str,
        temperature: float = 0.2,
        allow_fallback: bool = False,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        log_context: dict[str, object] | None = None,
    ) -> LLMTextResult:
        phase = str((log_context or {}).get("phase") or "").strip()
        if phase == "expert_selection":
            return _result(
                """
                {
                  "selected_experts": [
                    {"expert_id": "correctness_business", "reason": "覆盖业务正确性主路径", "confidence": 0.92},
                    {"expert_id": "security_compliance", "reason": "覆盖权限与输入校验风险", "confidence": 0.88},
                    {"expert_id": "database_analysis", "reason": "覆盖数据持久化和迁移影响", "confidence": 0.82}
                  ],
                  "skipped_experts": []
                }
                """.strip(),
                phase,
                resolution=resolution,
            )
        if phase in {"routing_plan", "impact_analysis_report", "template_schema"}:
            return _result(fallback_text, phase, resolution=resolution)
        if phase == "expert_review":
            file_path = str((log_context or {}).get("file_path") or "src/demo/example_service.py").strip()
            line_start = int((log_context or {}).get("line_start") or 18)
            return _result(
                json.dumps(
                    {
                        "findings": [
                            {
                                "file_path": file_path,
                                "title": "新增分支缺少失败路径处理",
                                "claim": "新增订单创建分支在保存失败时没有返回明确错误，调用方可能误判为创建成功。",
                                "finding_type": "direct_defect",
                                "normalized_issue_type": "missing_failure_handling",
                                "severity": "high",
                                "confidence": 0.93,
                                "line_start": line_start,
                                "line_end": line_start,
                                "evidence": [
                                    "新增 create 调用没有处理 repository.save 失败或异常语义。",
                                    "当前 diff 命中订单创建主流程。",
                                ],
                                "cross_file_evidence": ["调用方依赖创建失败时返回明确错误语义。"],
                                "assumptions": [],
                                "context_files": [file_path],
                                "direct_evidence": True,
                                "matched_rules": ["失败路径必须显式处理"],
                                "violated_guidelines": ["业务失败语义不能被吞掉"],
                                "rule_based_reasoning": "新增业务分支改变成功/失败语义时，应明确处理失败路径。",
                                "fix_strategy": "在保存失败时返回明确错误或抛出领域异常。",
                                "suggested_fix": "补齐保存失败的错误处理，并用测试覆盖失败分支。",
                                "change_steps": ["捕获或判断保存失败结果", "返回明确错误语义", "补充失败路径单元测试"],
                                "suggested_code": "if (!repository.save(order)) { throw new OrderCreateException(\"create failed\"); }",
                                "verification_needed": False,
                                "verification_plan": "",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                phase,
                resolution=resolution,
            )
        if phase == "debate":
            return _result(
                "回应主Agent: 已复核。\n风险结论: 该问题有直接代码证据。\n修复建议: 补齐失败路径处理。",
                phase,
                resolution=resolution,
            )
        if phase == "final_summary":
            return _result("主Agent收敛完成：本轮已形成稳定检视结果，请优先处理高风险议题。", phase, resolution=resolution)
        return _result(fallback_text or "测试 LLM 响应", phase, resolution=resolution)

    monkeypatch.setattr(
        "app.services.llm_chat_service.LLMChatService.complete_text",
        _fake_complete_text,
    )


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.json"))
    return Path(os.environ["STORAGE_ROOT"])


@pytest.fixture
def client(storage_root: Path) -> TestClient:
    import app.config
    import app.services.review_service
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.services.review_service)
    importlib.reload(app.main)

    return TestClient(app.main.create_application())
