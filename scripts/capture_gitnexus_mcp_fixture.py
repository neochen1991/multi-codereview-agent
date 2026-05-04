from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.gitnexus_impact_service import GitNexusImpactService, GitNexusMcpImpactClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a real GitNexus MCP payload for a local repository. "
            "The output can be committed under backend/tests/fixtures/gitnexus_mcp/ "
            "and used to protect parser compatibility."
        )
    )
    parser.add_argument("--repo-path", required=True, help="Local Git repository path already indexed by GitNexus.")
    parser.add_argument("--repo-name", default="", help="GitNexus repo name. Defaults to the repository directory name.")
    parser.add_argument("--source-ref", default="", help="Source ref or commit for symbol extraction.")
    parser.add_argument("--target-ref", default="", help="Target ref or commit for symbol extraction.")
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path relative to the repo. Can be provided multiple times.",
    )
    parser.add_argument("--diff-file", default="", help="Optional unified diff file to improve symbol extraction.")
    parser.add_argument(
        "--output",
        default="backend/tests/fixtures/gitnexus_mcp/captured-payload.json",
        help="Output JSON path.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=45, help="GitNexus MCP timeout.")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Only print GitNexus preflight diagnostics without calling MCP.",
    )
    return parser.parse_args()


def _read_diff(path: str) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8")


def _build_subject(args: argparse.Namespace, repo_path: Path) -> ReviewSubject:
    return ReviewSubject(
        subject_type="mr",
        repo_id=args.repo_name or repo_path.name,
        project_id="gitnexus_fixture_capture",
        source_ref=str(args.source_ref or "").strip(),
        target_ref=str(args.target_ref or "").strip(),
        changed_files=[str(item).strip() for item in list(args.changed_file or []) if str(item).strip()],
        unified_diff=_read_diff(str(args.diff_file or "").strip()),
        metadata={"workspace_repo_path": str(repo_path)},
    )


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    args = _parse_args()
    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        print(f"repo path does not exist or is not a directory: {repo_path}", file=sys.stderr)
        return 2

    subject = _build_subject(args, repo_path)
    runtime = RuntimeSettings(code_repo_local_path=str(repo_path))
    service = GitNexusImpactService()
    diagnostics = service.preflight(subject, runtime)
    if args.preflight_only:
        _print_json(diagnostics)
        return 0 if diagnostics.get("status") == "ready" else 1
    if diagnostics.get("status") != "ready":
        print("GitNexus preflight is not ready; run with --preflight-only for details.", file=sys.stderr)
        _print_json(diagnostics)
        return 1

    changed_symbols = service._build_changed_symbols(subject, runtime)
    client = GitNexusMcpImpactClient(timeout_seconds=args.timeout_seconds)
    payload = client.analyze_mr(
        repo_name=str(args.repo_name or repo_path.name),
        repo_path=str(repo_path),
        subject=subject,
        changed_symbols=changed_symbols,
        runtime_env=service._gitnexus_runtime_env(subject, runtime),
    )
    output_payload = {
        "capture_metadata": {
            "repo_path": str(repo_path),
            "repo_name": str(args.repo_name or repo_path.name),
            "source_ref": str(args.source_ref or ""),
            "target_ref": str(args.target_ref or ""),
            "changed_files": list(subject.changed_files or []),
            "changed_symbols": [item.model_dump(mode="json") for item in changed_symbols],
            "preflight_status": diagnostics.get("status"),
        },
        **payload,
    }
    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"captured GitNexus MCP payload: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
