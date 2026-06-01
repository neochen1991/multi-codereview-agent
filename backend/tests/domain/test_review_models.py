from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.finding import ExpertFindingPayload, ReviewFinding


def test_review_task_can_wrap_review_subject():
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_1",
        project_id="proj_1",
        source_ref="feature/demo",
        target_ref="main",
    )
    task = ReviewTask(review_id="rev_1", subject=subject, status="pending")
    assert task.subject.source_ref == "feature/demo"
    assert task.status == "pending"


def test_review_finding_accepts_candidate_anchor_metadata():
    finding = ReviewFinding(
        review_id="rev_1",
        expert_id="security_compliance",
        title="资源归属校验缺失",
        summary="使用请求 userId 查询订单。",
        risk_domain="security",
        method_name="createOrder",
        code_anchor="orderRepository.findByUserId(userId)",
        evidence_anchor_status="passed",
        evidence_anchor_reason="锚点通过",
    )

    assert finding.risk_domain == "security"
    assert finding.method_name == "createOrder"
    assert finding.code_anchor == "orderRepository.findByUserId(userId)"


def test_expert_payload_accepts_candidate_anchor_metadata():
    payload = ExpertFindingPayload.model_validate(
        {
            "title": "资源归属校验缺失",
            "claim": "使用请求 userId 查询订单。",
            "finding_type": "direct_defect",
            "severity": "high",
            "risk_domain": "security",
            "file_path": "OrderController.java",
            "line_start": 12,
            "method_name": "createOrder",
            "code_anchor": "findByUserId(userId)",
        }
    )

    assert payload.risk_domain == "security"
    assert payload.method_name == "createOrder"
    assert payload.code_anchor == "findByUserId(userId)"
