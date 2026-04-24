from app.services.orchestrator.nodes.judge_and_merge import judge_and_merge
from app.services.orchestrator.nodes.run_targeted_debate import run_targeted_debate
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMTextResult


def test_targeted_debate_accepts_multi_expert_direct_evidence():
    state = {
        "conflicts": [
            {
                "issue_id": "iss_direct",
                "title": "循环内逐条调用仓储导致批量路径放大",
                "summary": "多个专家都指向同一处循环内查库问题。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.86,
                "direct_evidence": True,
                "participant_expert_ids": ["performance_reliability", "database_analysis"],
                "expert_views": [
                    {"expert_id": "performance_reliability", "summary": "循环体内外部调用放大"},
                    {"expert_id": "database_analysis", "summary": "循环体内逐条查询"},
                ],
                "evidence": ["for (Order item : orders)", "repository.findById(item.id())"],
                "cross_file_evidence": ["OrderRepository.findById"],
                "context_files": ["OrderService.java"],
                "assumptions": [],
            }
        ]
    }

    result = run_targeted_debate(state)

    issue = result["issues"][0]
    assert issue["needs_debate"] is True
    assert issue["debate_result"]["final_verdict"] == "accept"
    assert issue["status"] == "debating"
    assert issue["confidence"] > 0.86


def test_targeted_debate_rejects_speculative_low_evidence_issue_before_judge():
    state = {
        "conflicts": [
            {
                "issue_id": "iss_speculative",
                "title": "需要确认调用方是否可能传空",
                "summary": "如果外部调用传空，可能 NPE。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.48,
                "direct_evidence": False,
                "participant_expert_ids": ["correctness_business"],
                "expert_views": [
                    {"expert_id": "correctness_business", "summary": "需要确认调用方是否传空"}
                ],
                "evidence": ["需要确认调用方是否传空"],
                "cross_file_evidence": [],
                "context_files": [],
                "assumptions": ["调用方可能传空"],
            }
        ]
    }

    debated = run_targeted_debate(state)
    judged = judge_and_merge(debated)

    issue = debated["issues"][0]
    assert issue["debate_result"]["final_verdict"] == "reject"
    assert issue["status"] == "rejected_after_debate"
    assert judged["issues"] == []


def test_targeted_debate_uses_llm_judge_when_enabled(monkeypatch):
    captured_prompt = {}

    def _fake_complete_text(_self, **kwargs):
        captured_prompt["user_prompt"] = kwargs["user_prompt"]
        return LLMTextResult(
            text='{"final_verdict":"needs_human","consensus":"两个专家都指出同一处并发窗口","dissent":"需要确认锁粒度","confidence_adjustment":-0.04,"reason":"风险较高但仍需人工确认锁语义"}',
            mode="live",
            provider="test",
            model="debate-model",
            base_url="http://debate",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        "app.services.orchestrator.nodes.run_targeted_debate.LLMChatService.complete_text",
        _fake_complete_text,
    )

    state = {
        "runtime_settings": RuntimeSettings(enable_llm_targeted_debate=True),
        "conflicts": [
            {
                "issue_id": "iss_llm_debate",
                "title": "Redis 锁释放缺少 owner 校验",
                "summary": "释放锁时直接 delete key。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.82,
                "direct_evidence": True,
                "participant_expert_ids": ["redis_analysis", "correctness_business"],
                "expert_views": [
                    {"expert_id": "redis_analysis", "summary": "delete lock key 未校验 owner"},
                    {"expert_id": "correctness_business", "summary": "并发任务可能释放他人锁"},
                ],
                "evidence": ["redisTemplate.delete(lockKey)"],
                "cross_file_evidence": ["tryLock 使用 requestId 写入锁值"],
                "context_files": ["RedisLockService.java"],
                "assumptions": [],
            }
        ],
    }

    result = run_targeted_debate(state)

    issue = result["issues"][0]
    assert "redis_analysis" in captured_prompt["user_prompt"]
    assert issue["status"] == "needs_human"
    assert issue["resolution"] == "targeted_debate_needs_human"
    assert issue["debate_result"]["mode"] == "live"
    assert issue["debate_result"]["final_verdict"] == "needs_human"


def test_targeted_debate_records_multi_round_refinement_when_configured(monkeypatch):
    calls = []

    def _fake_complete_text(_self, **kwargs):
        calls.append(kwargs["user_prompt"])
        if len(calls) == 1:
            return LLMTextResult(
                text='{"refined_expert_views":[{"expert_id":"performance_reliability","summary":"循环内调用 repository 是直接证据，应保留"},{"expert_id":"database_analysis","summary":"该问题同时构成 N+1 查询"}],"round_summary":"专家观点已补齐证据边界"}',
                mode="live",
                provider="test",
                model="debate-model",
                base_url="http://debate",
                api_key_env="TEST_KEY",
            )
        return LLMTextResult(
            text='{"final_verdict":"accept","consensus":"两个专家收敛为循环内查询放大","dissent":"","confidence_adjustment":0.05,"reason":"证据充分"}',
            mode="live",
            provider="test",
            model="debate-model",
            base_url="http://debate",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        "app.services.orchestrator.nodes.run_targeted_debate.LLMChatService.complete_text",
        _fake_complete_text,
    )

    state = {
        "runtime_settings": RuntimeSettings(enable_llm_targeted_debate=True, default_max_debate_rounds=2),
        "conflicts": [
            {
                "issue_id": "iss_multi_round",
                "title": "循环内逐条调用仓储导致批量路径放大",
                "summary": "多个专家都指向同一处循环内查库问题。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.84,
                "direct_evidence": True,
                "participant_expert_ids": ["performance_reliability", "database_analysis"],
                "expert_views": [
                    {"expert_id": "performance_reliability", "summary": "循环体内外部调用放大"},
                    {"expert_id": "database_analysis", "summary": "循环体内逐条查询"},
                ],
                "evidence": ["for (Order item : orders)", "repository.findById(item.id())"],
            }
        ],
    }

    result = run_targeted_debate(state)

    issue = result["issues"][0]
    assert len(calls) == 2
    assert issue["debate_result"]["final_verdict"] == "accept"
    assert issue["debate_result"]["round_count"] == 2
    assert issue["debate_result"]["rounds"][0]["phase"] == "expert_refinement"
    assert issue["expert_views"][0]["summary"] == "循环内调用 repository 是直接证据，应保留"
