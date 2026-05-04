from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.services.review_service import ReviewService


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "export_review_eval_result.py"


def _load_export_module():
    spec = importlib.util.spec_from_file_location("export_review_eval_result", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_export_review_eval_result_writes_report_json(storage_root: Path, tmp_path: Path) -> None:
    module = _load_export_module()
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_export",
            "project_id": "proj_export",
            "source_ref": "feature/export",
            "target_ref": "main",
            "title": "export eval result",
            "changed_files": ["backend/app/payments/service.py"],
        }
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_export_1",
        expert_id="security_compliance",
        title="Missing authorization",
        summary="Payment update no longer verifies owner_id authorization.",
        severity="high",
        finding_type="direct_defect",
        file_path="backend/app/payments/service.py",
        line_start=42,
        evidence=["owner_id check is absent"],
    )
    service.finding_repo.save(review.review_id, finding)
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                issue_id="iss_export_1",
                title="Missing authorization",
                summary="Payment update no longer verifies owner_id authorization.",
                severity="high",
                file_path="backend/app/payments/service.py",
                line_start=42,
                finding_ids=[finding.finding_id],
                evidence_chain=[
                    {"step": "claim", "status": "present"},
                    {"step": "anchor", "status": "anchored"},
                ],
            )
        ],
    )

    output_path = module.export_review_eval_result(
        storage_root=storage_root,
        review_id=review.review_id,
        output_dir=tmp_path / "results",
        case_id="payment-export",
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert output_path.name == "payment-export.json"
    assert payload["review_id"] == review.review_id
    assert payload["metadata"]["eval_case_id"] == "payment-export"
    assert payload["metadata"]["source_review_id"] == review.review_id
    assert payload["findings"][0]["title"] == "Missing authorization"
    assert payload["issues"][0]["evidence_chain"][0]["step"] == "claim"
    assert payload["confidence_summary"]["evidence_chain_issue_count"] == 1


def test_export_review_eval_results_exports_completed_reviews_by_default(storage_root: Path, tmp_path: Path) -> None:
    module = _load_export_module()
    service = ReviewService(storage_root=storage_root)
    completed = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_completed",
            "project_id": "proj_export",
            "source_ref": "feature/completed",
            "target_ref": "main",
            "title": "completed review",
            "changed_files": ["backend/app/completed.py"],
        }
    )
    completed.status = "completed"
    service.review_repo.save(completed)
    pending = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_pending",
            "project_id": "proj_export",
            "source_ref": "feature/pending",
            "target_ref": "main",
            "title": "pending review",
            "changed_files": ["backend/app/pending.py"],
        }
    )
    service.finding_repo.save(
        completed.review_id,
        ReviewFinding(
            review_id=completed.review_id,
            finding_id="fdg_completed",
            expert_id="correctness_business",
            title="Completed finding",
            summary="Completed review finding.",
            file_path="backend/app/completed.py",
            line_start=3,
        ),
    )

    output_paths = module.export_review_eval_results(
        storage_root=storage_root,
        output_dir=tmp_path / "batch-results",
    )

    assert [path.name for path in output_paths] == [f"{completed.review_id}.json"]
    assert not (tmp_path / "batch-results" / f"{pending.review_id}.json").exists()
    payload = json.loads(output_paths[0].read_text(encoding="utf-8"))
    assert payload["metadata"]["eval_case_id"] == completed.review_id
    assert payload["findings"][0]["title"] == "Completed finding"


def test_export_review_eval_results_supports_explicit_review_ids(storage_root: Path, tmp_path: Path) -> None:
    module = _load_export_module()
    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_explicit",
            "project_id": "proj_export",
            "source_ref": "feature/explicit",
            "target_ref": "main",
            "title": "explicit review",
            "changed_files": ["backend/app/explicit.py"],
        }
    )

    output_paths = module.export_review_eval_results(
        storage_root=storage_root,
        output_dir=tmp_path / "explicit-results",
        review_ids=[review.review_id],
    )

    assert [path.name for path in output_paths] == [f"{review.review_id}.json"]
