from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.review_service import ReviewService  # noqa: E402


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_review_eval_result(
    *,
    storage_root: Path | str,
    review_id: str,
    output_dir: Path | str,
    case_id: str | None = None,
    findings_limit: int | None = None,
    issues_limit: int | None = None,
) -> Path:
    service = ReviewService(storage_root=Path(storage_root))
    return export_review_eval_result_from_service(
        service=service,
        review_id=review_id,
        output_dir=output_dir,
        case_id=case_id,
        findings_limit=findings_limit,
        issues_limit=issues_limit,
    )


def export_review_eval_result_from_service(
    *,
    service: ReviewService,
    review_id: str,
    output_dir: Path | str,
    case_id: str | None = None,
    findings_limit: int | None = None,
    issues_limit: int | None = None,
) -> Path:
    report = service.build_report(
        review_id,
        findings_limit=findings_limit,
        issues_limit=issues_limit,
    )
    payload = report.model_dump(mode="json")
    payload["metadata"] = {
        **dict(payload.get("metadata") or {}),
        "eval_case_id": str(case_id or review_id),
        "source_review_id": review_id,
        "exported_at": datetime.now(UTC).isoformat(),
    }
    output_path = Path(output_dir) / f"{case_id or review_id}.json"
    write_json(output_path, payload)
    return output_path


def export_review_eval_results(
    *,
    storage_root: Path | str,
    output_dir: Path | str,
    review_ids: list[str] | None = None,
    statuses: set[str] | None = None,
    limit: int | None = None,
    findings_limit: int | None = None,
    issues_limit: int | None = None,
) -> list[Path]:
    service = ReviewService(storage_root=Path(storage_root))
    selected_ids = [str(item).strip() for item in list(review_ids or []) if str(item).strip()]
    if not selected_ids:
        allowed_statuses = {str(item).strip().lower() for item in (statuses or {"completed"}) if str(item).strip()}
        reviews = [
            review
            for review in service.list_reviews()
            if not allowed_statuses or str(review.status or "").strip().lower() in allowed_statuses
        ]
        if limit is not None:
            reviews = reviews[: max(0, int(limit))]
        selected_ids = [review.review_id for review in reviews]
    exported: list[Path] = []
    for review_id in selected_ids:
        exported.append(
            export_review_eval_result_from_service(
                service=service,
                review_id=review_id,
                output_dir=output_dir,
                case_id=review_id,
                findings_limit=findings_limit,
                issues_limit=issues_limit,
            )
        )
    return exported


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a stored review report as an eval result JSON file.")
    parser.add_argument("--storage-root", required=True, help="Storage root that contains the review database.")
    parser.add_argument("--review-id", action="append", help="Review id to export. Repeat to export multiple reviews.")
    parser.add_argument("--all", action="store_true", help="Export reviews selected by status filters.")
    parser.add_argument(
        "--status",
        action="append",
        help="Status to include when using --all. Repeatable. Defaults to completed.",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of reviews to export when using --all.")
    parser.add_argument(
        "--output-dir",
        default="backend/tests/fixtures/review_eval_results",
        help="Directory to write the exported result JSON.",
    )
    parser.add_argument("--case-id", help="Golden case id / output filename stem. Defaults to review id.")
    parser.add_argument("--findings-limit", type=int, help="Optional cap for exported findings.")
    parser.add_argument("--issues-limit", type=int, help="Optional cap for exported issues.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_ids = [str(item).strip() for item in list(args.review_id or []) if str(item).strip()]
    if args.all:
        output_paths = export_review_eval_results(
            storage_root=Path(args.storage_root),
            output_dir=Path(args.output_dir),
            review_ids=[],
            statuses={str(item).strip().lower() for item in (args.status or ["completed"]) if str(item).strip()},
            limit=args.limit,
            findings_limit=args.findings_limit,
            issues_limit=args.issues_limit,
        )
        print(json.dumps({"exported_count": len(output_paths), "paths": [str(path) for path in output_paths]}, ensure_ascii=False, indent=2))
        return 0
    if not review_ids:
        raise SystemExit("--review-id or --all is required")
    if args.case_id and len(review_ids) > 1:
        raise SystemExit("--case-id can only be used with a single --review-id")
    if len(review_ids) == 1:
        output_path = export_review_eval_result(
            storage_root=Path(args.storage_root),
            review_id=review_ids[0],
            output_dir=Path(args.output_dir),
            case_id=args.case_id,
            findings_limit=args.findings_limit,
            issues_limit=args.issues_limit,
        )
        print(str(output_path))
        return 0
    output_paths = export_review_eval_results(
        storage_root=Path(args.storage_root),
        output_dir=Path(args.output_dir),
        review_ids=review_ids,
        findings_limit=args.findings_limit,
        issues_limit=args.issues_limit,
    )
    print(json.dumps({"exported_count": len(output_paths), "paths": [str(path) for path in output_paths]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
