from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval_review_quality.py"


def _load_eval_module():
    spec = importlib.util.spec_from_file_location("eval_review_quality", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evaluate_review_quality_reports_recall_precision_noise_and_cost() -> None:
    module = _load_eval_module()
    case = {
        "case_id": "payment-auth-regression",
        "expected_findings": [
            {
                "id": "missing-owner-check",
                "severity": "P1",
                "file_path": "backend/app/payments/service.py",
                "keywords": ["owner_id", "authorization"],
            },
            {
                "id": "missing-rollback",
                "severity": "P2",
                "file_path": "backend/app/payments/service.py",
                "keywords": ["rollback", "transaction"],
            },
        ],
    }
    report = {
        "findings": [
            {
                "id": "f1",
                "severity": "P1",
                "file_path": "backend/app/payments/service.py",
                "title": "Missing owner_id authorization check",
                "summary": "The update path no longer verifies owner_id before charging.",
                "expert_id": "security_compliance",
                "evidence_chain": [
                    {"step": "claim", "status": "present"},
                    {"step": "anchor", "status": "anchored"},
                    {"step": "confidence", "status": "verified"},
                ],
                "evidence_anchor_status": "valid",
                "remediation_suggestion": "Restore the owner_id authorization check before charging.",
            },
            {
                "id": "f2",
                "severity": "P1",
                "file_path": "backend/app/payments/service.py",
                "title": "Missing owner_id authorization check",
                "summary": "Duplicate comment for the same authorization defect.",
                "expert_id": "correctness_business",
                "remediation_suggestion": "Restore the owner_id authorization check before charging.",
            },
            {
                "id": "f3",
                "severity": "P2",
                "file_path": "backend/app/payments/service.py",
                "title": "Transaction rollback is skipped",
                "summary": "The transaction can commit after a downstream failure without rollback.",
                "expert_id": "database_analysis",
                "evidence_chain": [
                    {"step": "claim", "status": "present"},
                    {"step": "anchor", "status": "anchored"},
                    {"step": "confidence", "status": "verified"},
                ],
                "evidence_anchor_status": "valid",
                "remediation_suggestion": "Rollback or fail the transaction when downstream processing fails.",
            },
            {
                "id": "f4",
                "severity": "P1",
                "file_path": "frontend/src/App.tsx",
                "title": "Button copy could be shorter",
                "summary": "This is not part of the expected regression.",
                "expert_id": "frontend_accessibility",
                "remediation_suggestion": "当前未生成可直接落地的建议代码，请结合本条问题说明和修改思路处理。",
            },
        ],
        "metadata": {"token_cost_usd": 0.6, "runtime_seconds": 30.0},
    }

    result = module.evaluate_case(case, report)

    assert result["matched_required_count"] == 2
    assert result["critical_recall"] == 1.0
    assert result["required_recall"] == 1.0
    assert result["precision"] == 0.6667
    assert result["blocking_precision"] == 0.5
    assert result["false_positive_rate"] == 0.25
    assert result["duplicate_rate"] == 0.25
    assert result["evidence_chain_coverage"] == 0.5
    assert result["anchor_accuracy"] == 0.5
    assert result["display_quality_rate"] == 0.75
    assert result["token_cost_per_true_positive"] == 0.3
    assert result["runtime_seconds_per_review"] == 30.0


def test_evaluate_suite_loads_cases_and_reports_from_directories(tmp_path: Path) -> None:
    module = _load_eval_module()
    cases_dir = tmp_path / "cases"
    results_dir = tmp_path / "results"
    cases_dir.mkdir()
    results_dir.mkdir()
    (cases_dir / "security.json").write_text(
        json.dumps(
            {
                "case_id": "security",
                "expected_findings": [
                    {
                        "id": "missing-validation",
                        "severity": "P1",
                        "file_path": "api/users.py",
                        "keywords": ["validation"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (results_dir / "security.json").write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "rule_code": "SEC-001",
                        "severity": "P1",
                        "file_path": "api/users.py",
                        "title": "Validation removed",
                        "summary": "User input validation is no longer enforced.",
                        "remediation_suggestion": "Restore the validation guard and add a negative request test.",
                        "evidence_anchor_status": "valid",
                        "evidence_chain": [
                            {"step": "claim", "status": "present"},
                            {"step": "anchor", "status": "anchored"},
                            {"step": "confidence", "status": "verified"},
                        ],
                    }
                ],
                "metrics": {"total_token_cost_usd": 0.2, "elapsed_seconds": 8.5},
            }
        ),
        encoding="utf-8",
    )

    suite = module.evaluate_suite(cases_dir, results_dir)

    assert suite["summary"]["case_count"] == 1
    assert suite["summary"]["critical_recall"] == 1.0
    assert suite["summary"]["precision"] == 1.0
    assert suite["summary"]["blocking_precision"] == 1.0
    assert suite["summary"]["evidence_chain_coverage"] == 1.0
    assert suite["summary"]["anchor_accuracy"] == 1.0
    assert suite["summary"]["display_quality_rate"] == 1.0
    assert suite["summary"]["token_cost_per_true_positive"] == 0.2
    assert suite["summary"]["runtime_seconds_per_review"] == 8.5
    assert suite["cases"][0]["case_id"] == "security"


def test_evaluate_complex_java_web_transaction_case_tracks_anchor_and_display_quality() -> None:
    module = _load_eval_module()
    case = {
        "case_id": "java-web-order-payment-composite",
        "expected_findings": [
            {
                "id": "sql-injection",
                "severity": "P1",
                "file_path": "src/main/java/com/example/order/OrderRepository.java",
                "keywords": ["sql", "tenantId"],
            },
            {
                "id": "missing-owner-check",
                "severity": "P1",
                "file_path": "src/main/java/com/example/order/OrderController.java",
                "keywords": ["userId", "权限"],
            },
            {
                "id": "loop-save",
                "severity": "P2",
                "file_path": "src/main/java/com/example/order/BatchOrderService.java",
                "keywords": ["循环", "repository.save"],
            },
            {
                "id": "todo-contract",
                "severity": "P2",
                "file_path": "src/main/java/com/example/order/BatchOrderService.java",
                "keywords": ["TODO", "扣减库存"],
            },
            {
                "id": "exception-success",
                "severity": "P1",
                "file_path": "src/main/java/com/example/payment/PaymentSettlementService.java",
                "keywords": ["catch", "success"],
            },
        ],
    }
    report = {
        "issues": [
            {
                "severity": "P1",
                "file_path": "src/main/java/com/example/order/OrderRepository.java",
                "line_start": 18,
                "title": "订单查询直接拼接 tenantId，存在 SQL 注入和租户越权风险",
                "summary": "OrderRepository 第 18 行把 tenantId 拼进 SQL 字符串，攻击者可扩大查询范围。",
                "current_code": "18 | +        return jdbc.query(\"select * from orders where tenant_id=\" + tenantId, mapper);",
                "remediation_suggestion": "改为参数化查询，并补充非法 tenantId 的回归用例。",
                "evidence_anchor_status": "valid",
            },
            {
                "severity": "P1",
                "file_path": "src/main/java/com/example/order/OrderController.java",
                "line_start": 27,
                "title": "订单详情接口信任传入 userId，缺少资源归属校验",
                "summary": "OrderController 第 27 行直接使用请求参数 userId 查询订单，当前代码没有和登录用户做权限绑定。",
                "current_code": "27 | +        return orderService.findByUserId(userId);",
                "remediation_suggestion": "从登录态读取用户身份，并校验订单归属后再返回详情。",
                "evidence_anchor_status": "valid",
            },
            {
                "severity": "P2",
                "file_path": "src/main/java/com/example/order/BatchOrderService.java",
                "line_start": 42,
                "title": "批量订单保存退化为循环逐条保存",
                "summary": "BatchOrderService 第 42 行在循环中调用 repository.save，批量输入会放大为 N 次数据库写入。",
                "current_code": "42 | +            orderRepository.save(order);",
                "remediation_suggestion": "改回 saveAll 或固定批次写入，并覆盖大批量输入回归测试。",
                "evidence_anchor_status": "valid",
            },
            {
                "severity": "P2",
                "file_path": "src/main/java/com/example/order/BatchOrderService.java",
                "line_start": 47,
                "title": "TODO 里的库存扣减未实现",
                "summary": "BatchOrderService 第 47 行 TODO 承诺扣减库存，但当前实现只发布订单事件，没有库存扣减动作。",
                "current_code": "47 | +        // TODO 批量下单成功后扣减库存",
                "remediation_suggestion": "补齐库存扣减和失败回滚；如果本次不交付，应删除误导性 TODO。",
                "evidence_anchor_status": "valid",
            },
            {
                "severity": "P1",
                "file_path": "src/main/java/com/example/payment/PaymentSettlementService.java",
                "line_start": 63,
                "title": "支付捕获异常后仍返回成功",
                "summary": "PaymentSettlementService 第 63 行 catch RuntimeException 后返回 success，调用方会把失败当成结算成功。",
                "current_code": "63 | +        } catch (RuntimeException ignored) { return SettlementResult.success(payments.size()); }",
                "remediation_suggestion": "保留异常上下文并返回失败或抛出业务异常，不能在 catch 分支返回 success。",
                "evidence_anchor_status": "valid",
            },
        ],
        "metrics": {"elapsed_seconds": 42.0, "total_token_cost_usd": 0.5},
    }

    result = module.evaluate_case(case, report)

    assert result["required_recall"] == 1.0
    assert result["critical_recall"] == 1.0
    assert result["precision"] == 1.0
    assert result["blocking_precision"] == 1.0
    assert result["anchor_accuracy"] == 1.0
    assert result["display_quality_rate"] == 1.0
    assert result["duplicate_rate"] == 0.0
    assert result["runtime_seconds_per_review"] == 42.0


def test_quality_gates_record_violations_for_ci() -> None:
    module = _load_eval_module()
    suite = {
        "summary": {
            "required_recall": 0.8,
            "critical_recall": 0.5,
            "precision": 0.75,
            "blocking_precision": 1.0,
            "false_positive_rate": 0.25,
            "duplicate_rate": 0.0,
            "evidence_chain_coverage": 0.5,
            "anchor_accuracy": 0.4,
            "display_quality_rate": 0.6,
            "runtime_seconds_per_review": 12.0,
        },
        "cases": [],
    }

    gates = module.apply_quality_gates(
        suite,
        {
            "required_recall": 0.9,
            "critical_recall": 0.9,
            "precision": 0.8,
            "blocking_precision": 0.8,
            "evidence_chain_coverage": 0.8,
            "anchor_accuracy": 0.9,
            "display_quality_rate": 0.9,
            "max_false_positive_rate": 0.1,
            "max_duplicate_rate": 0.1,
            "max_runtime_seconds_per_review": 10.0,
        },
    )

    assert gates["passed"] is False
    assert suite["quality_gates"] == gates
    assert {item["metric"] for item in gates["violations"]} == {
        "required_recall",
        "critical_recall",
        "precision",
        "evidence_chain_coverage",
        "anchor_accuracy",
        "display_quality_rate",
        "false_positive_rate",
        "runtime_seconds_per_review",
    }
