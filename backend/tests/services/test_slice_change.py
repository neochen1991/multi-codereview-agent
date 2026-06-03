from app.services.orchestrator.nodes.slice_change import slice_change


def test_slice_change_builds_hunk_level_risk_slices():
    state = {
        "changed_files": ["src/main/java/app/OwnerController.java"],
        "unified_diff": "\n".join(
            [
                "diff --git a/src/main/java/app/OwnerController.java b/src/main/java/app/OwnerController.java",
                "--- a/src/main/java/app/OwnerController.java",
                "+++ b/src/main/java/app/OwnerController.java",
                "@@ -10,3 +10,3 @@ public class OwnerController {",
                "-    public String create(@Valid Owner owner, BindingResult result) {",
                "+    public String create(Owner owner, BindingResult result) {",
                "         return save(owner);",
                "@@ -30,3 +30,5 @@ public class OwnerController {",
                "+    for (Owner owner : owners) {",
                "+        ownerRepository.findById(owner.getId());",
                "+    }",
            ]
        ),
    }

    result = slice_change(state)

    assert result["phase"] == "slice_change"
    assert len(result["change_slices"]) == 2
    signals = {
        signal
        for item in result["change_slices"]
        for signal in item["risk_signals"]
    }
    assert "security_guard_removed" in signals
    assert "loop_call_amplification" in signals
    assert "security_surface" in result["risk_hints"]
    assert "database_migration" in result["risk_hints"]
    assert result["risk_candidates"]
    candidate_domains = {item["risk_domain"] for item in result["risk_candidates"]}
    candidate_experts = {item["suggested_expert_id"] for item in result["risk_candidates"]}
    assert "security" in candidate_domains
    assert "performance" in candidate_domains
    assert "security_compliance" in candidate_experts
    assert "performance_reliability" in candidate_experts
