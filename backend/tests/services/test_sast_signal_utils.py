from app.services.sast_signal_utils import (
    canonical_tool_observation_id,
    tool_observation_aliases,
    tool_references_match,
)


def test_sast_tool_observation_aliases_cover_canonical_legacy_short_and_windows_paths() -> None:
    observation = {
        "tool": "semgrep",
        "rule_id": "java.sql.concat-user-input",
        "observation_id": "semgrep:java.sql.concat-user-input:9",
        "file_path": r"src\main\java\demo\UserDao.java",
        "line_start": 9,
    }

    canonical = canonical_tool_observation_id(observation)
    aliases = tool_observation_aliases(observation)

    assert canonical == "sast:semgrep:java.sql.concat-user-input:src/main/java/demo/UserDao.java:9"
    assert "semgrep:java.sql.concat-user-input" in aliases
    assert "semgrep:java.sql.concat-user-input:9" in aliases
    assert "java.sql.concat-user-input" in aliases
    assert tool_references_match(canonical, "semgrep:java.sql.concat-user-input")
    assert tool_references_match(canonical, "semgrep:java.sql.concat-user-input:9")
