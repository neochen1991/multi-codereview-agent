from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.model_prompt_profiles import resolve_model_prompt_profile  # noqa: E402


@dataclass(frozen=True)
class WindowsReviewQualityCheck:
    check_id: str
    passed: bool
    details: str


def normalize_review_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", text):
        return text
    return text.lstrip("/")


def build_windows_review_quality_report(
    *,
    workspace_path: str,
    changed_files: list[str],
    prompt_text: str = "",
    metadata: dict[str, object] | None = None,
    model_name: str = "minimax-2.5",
    dry_run: bool = False,
) -> dict[str, object]:
    metadata = dict(metadata or {})
    workspace = Path(workspace_path) if workspace_path else Path()
    normalized_workspace = normalize_review_path(workspace_path)
    normalized_changed_files = [normalize_review_path(item) for item in changed_files if str(item or "").strip()]
    prompt_normalized = prompt_text.replace("\\", "/")
    profile = resolve_model_prompt_profile(model_name, profile_name="auto")

    checks = [
        _check(
            "path_with_spaces_supported",
            " " in workspace_path or dry_run,
            "workspace path should be accepted when it contains spaces",
        ),
        _check(
            "chinese_path_supported",
            any(ord(char) > 127 for char in workspace_path) or dry_run,
            "workspace path should be accepted when it contains non-ASCII characters",
        ),
        _check(
            "drive_path_normalized",
            bool(re.match(r"^[A-Za-z]:/", normalized_workspace)) or dry_run or os.name != "nt",
            f"normalized workspace={normalized_workspace}",
        ),
        _check(
            "changed_files_normalized",
            bool(normalized_changed_files) and all("\\" not in item for item in normalized_changed_files),
            f"changed_files={normalized_changed_files}",
        ),
        _check(
            "workspace_exists",
            dry_run or bool(workspace_path and workspace.exists()),
            f"workspace={workspace_path}",
        ),
        _check(
            "git_repo_readable",
            dry_run or _git_repo_readable(workspace),
            "git rev-parse must succeed in MR workspace",
        ),
        _check(
            "tree_sitter_graph_ready",
            dry_run
            or _graph_ready(metadata.get("tree_sitter_graph_result"))
            or _path_exists(metadata.get("code_graph_db_path"))
            or (workspace / ".code-review-graph" / "graph.db").exists(),
            "Tree-sitter graph must be ready or graph.db must exist",
        ),
        _check(
            "gitnexus_graph_ready",
            dry_run
            or _graph_ready(metadata.get("gitnexus_graph_result"))
            or _graph_ready(metadata.get("review_workspace_gitnexus_graph"))
            or (workspace / ".gitnexus").exists(),
            "GitNexus graph must be ready, or degradation must be explicit before review",
        ),
        _check(
            "prompt_workspace_matches_mr_workspace",
            dry_run
            or not prompt_text
            or normalized_workspace in prompt_normalized
            or str(workspace_path) in prompt_text,
            "expert prompt/replay should reference the MR workspace path",
        ),
        _check(
            "model_profile_is_rule_guided",
            profile.name in {"rule-guided-compact", "strict-json-small-context", "rule-guided-standard", "long-context-capable"},
            f"model={model_name}, profile={profile.name}",
        ),
    ]
    passed = all(item.passed for item in checks)
    return {
        "passed": passed,
        "workspace_path": workspace_path,
        "normalized_workspace_path": normalized_workspace,
        "normalized_changed_files": normalized_changed_files,
        "model_name": model_name,
        "prompt_profile": profile.name,
        "checks": [asdict(item) for item in checks],
        "missing": [item.check_id for item in checks if not item.passed],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Windows review-quality acceptance checks.")
    parser.add_argument("--workspace", required=True, help="MR workspace path. Can be a Windows drive path.")
    parser.add_argument("--changed-file", dest="changed_files", action="append", default=[])
    parser.add_argument("--metadata-json", default="", help="JSON string or path containing review metadata.")
    parser.add_argument("--prompt-file", default="", help="Optional saved expert prompt/replay file.")
    parser.add_argument("--model", default="minimax-2.5")
    parser.add_argument("--dry-run", action="store_true", help="Validate normalization/profile logic without requiring local graph files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata = _load_metadata(args.metadata_json)
    prompt_text = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else ""
    report = build_windows_review_quality_report(
        workspace_path=args.workspace,
        changed_files=args.changed_files,
        prompt_text=prompt_text,
        metadata=metadata,
        model_name=args.model,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


def _check(check_id: str, passed: bool, details: str) -> WindowsReviewQualityCheck:
    return WindowsReviewQualityCheck(check_id=check_id, passed=bool(passed), details=details)


def _load_metadata(value: str) -> dict[str, object]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    candidate = Path(raw)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(raw)


def _git_repo_readable(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _path_exists(value: object) -> bool:
    raw = str(value or "").strip()
    return bool(raw and Path(raw).exists())


def _graph_ready(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    status = str(value.get("status") or value.get("state") or value.get("phase") or "").strip().lower()
    return status in {"ready", "passed", "success", "ok", "available"} or bool(value.get("ready")) is True


if __name__ == "__main__":
    raise SystemExit(main())
