from pathlib import Path

from app.services.review_runner import ReviewRunner


def test_review_runner_persists_debate_messages_for_conflicted_issue(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()

    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)
    assert any(item.message_type == "debate_message" for item in messages)
    assert any(item.expert_id == "judge" for item in messages)


def test_review_runner_persists_main_agent_coordination_messages(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()

    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)
    assert any(
        item.expert_id == "main_agent" and item.message_type == "main_agent_command"
        for item in messages
    )
    assert any(
        item.expert_id == "main_agent" and item.message_type == "main_agent_summary"
        for item in messages
    )
    assert any(item.message_type == "expert_ack" for item in messages)
    assert any(item.message_type == "expert_analysis" for item in messages)
    command = next(
        item for item in messages if item.expert_id == "main_agent" and item.message_type == "main_agent_command"
    )
    assert command.metadata["file_path"]
    assert command.metadata["line_start"] >= 1
    assert command.metadata["target_expert_id"]
    analysis = next(item for item in messages if item.message_type == "expert_analysis")
    assert analysis.metadata["reply_to_expert_id"] == "main_agent"
    assert analysis.metadata["model"] == "kimi-k2.5"
    assert "allowed_tools" in analysis.metadata
    assert "knowledge_sources" in analysis.metadata
    assert "skill_results" in analysis.metadata
    assert any(item.message_type == "expert_tool_call" for item in messages)
    assert any(item.message_type == "expert_skill_call" for item in messages)
