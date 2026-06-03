from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
DEFAULT_MANIFEST_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "java_cases" / "cases.json"
DEFAULT_CACHE_ROOT = Path("/tmp/java-review-eval-cache")
DEFAULT_WORKSPACE_ROOT = Path("/tmp/java-review-eval-workspaces")
DEFAULT_API_BASE = "http://127.0.0.1:8011/api"
FIXTURE_MARKER_FILE = ".codereview-fixture.json"
FIXTURE_VERSION = 4

from eval_review_quality import evaluate_case as evaluate_quality_case  # noqa: E402
from validate_windows_review_quality import build_windows_review_quality_report  # noqa: E402


@dataclass(frozen=True)
class RepoDefinition:
    repo_key: str
    clone_url: str
    default_branch: str
    review_mode: str
    preferred_local_path: str = ""


@dataclass(frozen=True)
class PatchOperation:
    path: str
    search: str
    replace: str
    description: str = ""


@dataclass(frozen=True)
class ExpectedOutcome:
    required_experts: tuple[str, ...]
    rule_ids_any_of: tuple[str, ...]
    finding_keywords: tuple[str, ...]
    problem_markers: tuple[dict[str, object], ...] = ()
    min_findings: int = 1
    min_issues: int = 0


@dataclass(frozen=True)
class JavaReviewCase:
    case_id: str
    repo_key: str
    category: str
    scenario: str
    business_context: str
    tags: tuple[str, ...]
    patch_operations: tuple[PatchOperation, ...]
    expected: ExpectedOutcome


@dataclass(frozen=True)
class MaterializedCase:
    case: JavaReviewCase
    repository: RepoDefinition
    workspace_repo: Path
    changed_files: tuple[str, ...]
    unified_diff: str
    graph_metadata: dict[str, object] | None = None

    def to_review_payload(self, analysis_mode: str = "light") -> dict[str, object]:
        metadata: dict[str, object] = {
            "trigger_source": "manual_real_case_test",
            "java_eval_case_id": self.case.case_id,
            "java_eval_category": self.case.category,
            "java_review_mode_hint": self.repository.review_mode,
            "business_context": self.case.business_context,
            "workspace_repo_path": str(self.workspace_repo),
            "expected_rule_ids_any_of": list(self.case.expected.rule_ids_any_of),
            "expected_finding_keywords": list(self.case.expected.finding_keywords),
        }
        if self.graph_metadata:
            metadata.update(self.graph_metadata)
        return {
            "subject_type": "mr",
            "analysis_mode": analysis_mode,
            "repo_id": self.repository.repo_key,
            "project_id": "java-eval-suite",
            "source_ref": f"bench/{self.case.case_id}",
            "target_ref": self.repository.default_branch,
            "title": self.case.scenario,
            "repo_url": self.repository.clone_url,
            "selected_experts": list(self.case.expected.required_experts),
            "changed_files": list(self.changed_files),
            "unified_diff": self.unified_diff,
            "metadata": metadata,
        }


@dataclass(frozen=True)
class BenchmarkScore:
    passed: bool
    score: float
    required_expert_coverage: float
    required_rule_hit: bool
    finding_keyword_coverage: float
    input_quality_coverage: float
    problem_marker_coverage: float
    invalid_finding_rate: float
    missing_experts: tuple[str, ...]
    matched_rule_ids: tuple[str, ...]
    missing_keywords: tuple[str, ...]
    missing_problem_markers: tuple[str, ...]
    missing_input_sections: tuple[str, ...]
    schema_valid_rate: float = 1.0
    rule_coverage_rate: float = 1.0
    context_hit_rate: float = 1.0
    timeout_rate: float = 0.0
    incomplete: bool = False
    review_status: str = ""
    review_phase: str = ""


KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    "aggregate": ("aggregate", "聚合", "聚合根"),
    "factory": ("factory", "工厂", "工厂方法", "create 工厂", "course.create"),
    "domain event": ("domain event", "domain events", "领域事件", "事件发布", "pulldomainevents"),
    "catch": ("catch", "异常处理", "吞异常", "静默吞", "空 catch", "printstacktrace"),
}

REQUIRED_BENCHMARK_PROBLEM_COVERAGE: dict[str, tuple[str, ...]] = {
    "ddd_aggregate_factory_bypass": ("aggregate", "factory"),
    "domain_event_order": ("domain event", "repository.save", "publish"),
    "exception_swallowed": ("catch", "异常"),
    "criteria_query_semantics": ("equal", "like", "criteria"),
    "batch_limit_removed": ("LIMIT", "全表"),
    "compile_error": ("compile", "编译", "semicolon"),
    "mq_idempotency_removed": ("mq", "ack", "幂等"),
    "redis_cache_ttl_missing": ("redis", "ttl", "缓存"),
    "high_risk_change_without_test": ("测试", "覆盖", "高风险"),
}


def _build_score_summary(score: BenchmarkScore) -> str:
    verdict = "INCOMPLETE" if score.incomplete else ("PASS" if score.passed else "FAIL")
    parts = [f"{verdict} ({score.score:.3f})"]
    if score.incomplete:
        parts.append(f"status={score.review_status or 'unknown'}")
        parts.append(f"phase={score.review_phase or 'unknown'}")
    parts.append(f"experts={score.required_expert_coverage:.2f}")
    parts.append(f"rules={'hit' if score.required_rule_hit else 'miss'}")
    parts.append(f"keywords={score.finding_keyword_coverage:.2f}")
    parts.append(f"markers={score.problem_marker_coverage:.2f}")
    parts.append(f"inputs={score.input_quality_coverage:.2f}")
    parts.append(f"invalid={score.invalid_finding_rate:.2f}")
    parts.append(f"schema={score.schema_valid_rate:.2f}")
    parts.append(f"rulecov={score.rule_coverage_rate:.2f}")
    parts.append(f"context={score.context_hit_rate:.2f}")
    parts.append(f"timeouts={score.timeout_rate:.2f}")
    if score.missing_experts:
        parts.append(f"missing_experts={','.join(score.missing_experts)}")
    if score.missing_keywords:
        parts.append(f"missing_keywords={','.join(score.missing_keywords[:3])}")
    if score.missing_problem_markers:
        parts.append(f"missing_markers={','.join(score.missing_problem_markers[:2])}")
    if score.missing_input_sections:
        parts.append(f"missing_inputs={','.join(score.missing_input_sections[:3])}")
    return " | ".join(parts)


def _quality_eval_case_from_benchmark(case: JavaReviewCase) -> dict[str, object]:
    expected_findings: list[dict[str, object]] = []
    for index, marker in enumerate(case.expected.problem_markers):
        if not isinstance(marker, dict):
            continue
        keywords = [str(item) for item in list(marker.get("keywords") or []) if str(item).strip()]
        expected_findings.append(
            {
                "id": str(marker.get("id") or f"marker-{index + 1}"),
                "severity": str(marker.get("severity") or "P1"),
                "file_path": str(marker.get("file_path") or ""),
                "keywords": keywords,
            }
        )
    if not expected_findings:
        expected_findings = [
            {
                "id": f"keyword-{index + 1}",
                "severity": "P1",
                "keywords": [keyword],
            }
            for index, keyword in enumerate(case.expected.finding_keywords)
            if str(keyword).strip()
        ]
    return {
        "case_id": case.case_id,
        "expected_findings": expected_findings,
    }


def _final_issue_report_for_quality_eval(report: dict[str, object]) -> dict[str, object]:
    """Evaluate the published effective issue list, not raw expert observations.

    The benchmark score already checks raw findings for recall, expert coverage
    and rule traversal. Precision, duplicate rate and display quality should be
    measured on final issues because that is what developers act on.
    """

    return {
        "issues": [item for item in list(report.get("issues") or []) if isinstance(item, dict)],
        "findings": [],
        "metadata": dict(report.get("metadata") or {}) if isinstance(report.get("metadata"), dict) else {},
        "metrics": dict(report.get("metrics") or {}) if isinstance(report.get("metrics"), dict) else {},
        "runtime_seconds": report.get("runtime_seconds"),
        "elapsed_seconds": report.get("elapsed_seconds"),
        "token_cost_usd": report.get("token_cost_usd"),
        "total_token_cost_usd": report.get("total_token_cost_usd"),
    }


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, object]:
    return _read_json(path)


def load_repositories(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, RepoDefinition]:
    manifest = load_manifest(path)
    repositories = {}
    for item in manifest.get("repos", []):
        entry = RepoDefinition(
            repo_key=str(item["repo_key"]),
            clone_url=str(item["clone_url"]),
            default_branch=str(item.get("default_branch") or "main"),
            review_mode=str(item.get("review_mode") or "general"),
            preferred_local_path=str(item.get("preferred_local_path") or ""),
        )
        repositories[entry.repo_key] = entry
    return repositories


def load_cases(path: Path = DEFAULT_MANIFEST_PATH) -> list[JavaReviewCase]:
    manifest = load_manifest(path)
    cases: list[JavaReviewCase] = []
    for item in manifest.get("cases", []):
        patch_operations = tuple(
            PatchOperation(
                path=str(operation["path"]),
                search=str(operation["search"]),
                replace=str(operation["replace"]),
                description=str(operation.get("description") or ""),
            )
            for operation in item.get("patch_operations", [])
        )
        expected_raw = item.get("expected", {})
        cases.append(
            JavaReviewCase(
                case_id=str(item["case_id"]),
                repo_key=str(item["repo_key"]),
                category=str(item["category"]),
                scenario=str(item["scenario"]),
                business_context=str(item["business_context"]),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
                patch_operations=patch_operations,
                expected=ExpectedOutcome(
                    required_experts=tuple(str(value) for value in expected_raw.get("required_experts", [])),
                    rule_ids_any_of=tuple(str(value) for value in expected_raw.get("rule_ids_any_of", [])),
                    finding_keywords=tuple(str(value) for value in expected_raw.get("finding_keywords", [])),
                    problem_markers=tuple(
                        {
                            "file_path": str(marker.get("file_path") or ""),
                            "keywords": tuple(str(keyword) for keyword in marker.get("keywords", [])),
                        }
                        for marker in expected_raw.get("problem_markers", [])
                        if str(marker.get("file_path") or "").strip()
                    ),
                    min_findings=int(expected_raw.get("min_findings", 1)),
                    min_issues=int(expected_raw.get("min_issues", 0)),
                ),
            )
        )
    return cases


def select_cases(cases: list[JavaReviewCase], case_ids: list[str] | None = None) -> list[JavaReviewCase]:
    if not case_ids:
        return cases
    wanted = set(case_ids)
    selected = [item for item in cases if item.case_id in wanted]
    missing = sorted(wanted.difference(item.case_id for item in selected))
    if missing:
        raise KeyError(f"unknown case ids: {', '.join(missing)}")
    return selected


def validate_benchmark_problem_coverage(cases: list[JavaReviewCase]) -> dict[str, object]:
    coverage: dict[str, bool] = {}
    for problem_id, keywords in REQUIRED_BENCHMARK_PROBLEM_COVERAGE.items():
        required = {item.lower() for item in keywords}
        covered = False
        for case in cases:
            searchable = " ".join(
                [
                    case.case_id,
                    case.category,
                    case.scenario,
                    case.business_context,
                    " ".join(case.tags),
                    " ".join(case.expected.finding_keywords),
                    " ".join(
                        " ".join(str(keyword) for keyword in marker.get("keywords", ()))
                        for marker in case.expected.problem_markers
                    ),
                ]
            ).lower()
            if all(keyword.lower() in searchable for keyword in required):
                covered = True
                break
        coverage[problem_id] = covered
    missing = [problem_id for problem_id, covered in coverage.items() if not covered]
    return {
        "passed": not missing,
        "coverage": coverage,
        "missing": missing,
        "required_problem_count": len(REQUIRED_BENCHMARK_PROBLEM_COVERAGE),
        "case_count": len(cases),
    }


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def _is_valid_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def _is_current_fixture_repo(path: Path) -> bool:
    if not _is_valid_git_repo(path):
        return False
    marker_path = path / FIXTURE_MARKER_FILE
    if not marker_path.exists():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(marker.get("repo_key") or "") == "java-ddd-example" and int(marker.get("version") or 0) >= FIXTURE_VERSION


def _write_fixture_file(repo_path: Path, relative_path: str, content: str) -> None:
    target = repo_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _create_java_ddd_example_fixture_repo(repo_path: Path) -> None:
    """Create a small local Java repo that covers the DDD benchmark snippets.

    The real Windows/intranet deployment reviews actual business repositories.
    This fixture only prevents local benchmark/smoke runs from degrading because
    the historical `/tmp/java-ddd-example-review-repo` seed is absent.
    """

    if repo_path.exists() or repo_path.is_symlink():
        if repo_path.is_dir() and not repo_path.is_symlink():
            shutil.rmtree(repo_path)
        else:
            repo_path.unlink()
    repo_path.mkdir(parents=True, exist_ok=True)

    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
        """package tv.codely.mooc.courses.application.create;

import tv.codely.mooc.courses.domain.Course;
import tv.codely.mooc.courses.domain.CourseDuration;
import tv.codely.mooc.courses.domain.CourseId;
import tv.codely.mooc.courses.domain.CourseName;
import tv.codely.mooc.courses.domain.CourseRepository;
import tv.codely.shared.domain.bus.event.EventBus;

public final class CourseCreator {
    private final CourseRepository repository;
    private final EventBus eventBus;

    public CourseCreator(CourseRepository repository, EventBus eventBus) {
        this.repository = repository;
        this.eventBus = eventBus;
    }

    public void create(CourseId id, CourseName name, CourseDuration duration) {
        Course course = Course.create(id, name, duration);

        repository.save(course);
        eventBus.publish(course.pullDomainEvents());
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/Course.java",
        """package tv.codely.mooc.courses.domain;

import java.util.ArrayList;
import java.util.List;
import tv.codely.shared.domain.bus.event.DomainEvent;

public class Course {
    private final CourseId id;
    private final CourseName name;
    private final CourseDuration duration;
    private final List<DomainEvent> domainEvents = new ArrayList<>();

    public Course(CourseId id, CourseName name, CourseDuration duration) {
        this.id = id;
        this.name = name;
        this.duration = duration;
    }

    public static Course create(CourseId id, CourseName name, CourseDuration duration) {
        Course course = new Course(id, name, duration);
        course.record(new CourseCreatedDomainEvent(id.value(), name.value(), duration.value()));
        return course;
    }

    private void record(DomainEvent event) {
        domainEvents.add(event);
    }

    public List<DomainEvent> pullDomainEvents() {
        List<DomainEvent> events = new ArrayList<>(domainEvents);
        domainEvents.clear();
        return events;
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/CourseRepository.java",
        """package tv.codely.mooc.courses.domain;

public interface CourseRepository {
    void save(Course course);
}
""",
    )
    for class_name, value_type in (
        ("CourseId", "String"),
        ("CourseName", "String"),
        ("CourseDuration", "String"),
    ):
        _write_fixture_file(
            repo_path,
            f"src/mooc/main/tv/codely/mooc/courses/domain/{class_name}.java",
            f"""package tv.codely.mooc.courses.domain;

public final class {class_name} {{
    private final {value_type} value;

    public {class_name}({value_type} value) {{
        this.value = value;
    }}

    public {value_type} value() {{
        return value;
    }}
}}
""",
        )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/CourseCreatedDomainEvent.java",
        """package tv.codely.mooc.courses.domain;

import tv.codely.shared.domain.bus.event.DomainEvent;

public final class CourseCreatedDomainEvent implements DomainEvent {
    private final String id;
    private final String name;
    private final String duration;

    public CourseCreatedDomainEvent(String id, String name, String duration) {
        this.id = id;
        this.name = name;
        this.duration = duration;
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/domain/bus/event/DomainEvent.java",
        """package tv.codely.shared.domain.bus.event;

public interface DomainEvent {
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/domain/bus/event/EventBus.java",
        """package tv.codely.shared.domain.bus.event;

import java.util.List;

public interface EventBus {
    void publish(List<DomainEvent> events);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/domain/criteria/Filter.java",
        """package tv.codely.shared.domain.criteria;

public final class Filter {
    private final FilterField field;
    private final FilterValue value;

    public Filter(FilterField field, FilterValue value) {
        this.field = field;
        this.value = value;
    }

    public FilterField field() {
        return field;
    }

    public FilterValue value() {
        return value;
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/domain/criteria/FilterField.java",
        """package tv.codely.shared.domain.criteria;

public final class FilterField {
    private final String value;

    public FilterField(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/domain/criteria/FilterValue.java",
        """package tv.codely.shared.domain.criteria;

public final class FilterValue {
    private final String value;

    public FilterValue(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        """package tv.codely.shared.infrastructure.hibernate;

import jakarta.persistence.criteria.CriteriaBuilder;
import jakarta.persistence.criteria.Predicate;
import jakarta.persistence.criteria.Root;
import tv.codely.shared.domain.criteria.Filter;

public final class HibernateCriteriaConverter<T> {
    private final CriteriaBuilder builder;

    public HibernateCriteriaConverter(CriteriaBuilder builder) {
        this.builder = builder;
    }

    private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {
        return builder.equal(root.get(filter.field().value()), filter.value().value());
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        """package tv.codely.shared.infrastructure.bus.event.mysql;

import java.lang.reflect.InvocationTargetException;
import org.hibernate.SessionFactory;
import org.hibernate.query.NativeQuery;
import tv.codely.shared.domain.bus.event.DomainEvent;

public final class MySqlDomainEventsConsumer {
	private final Integer CHUNKS = 200;
	private final SessionFactory sessionFactory;

	public MySqlDomainEventsConsumer(SessionFactory sessionFactory) {
		this.sessionFactory = sessionFactory;
	}

	public void consume() {
		while (true) {
			NativeQuery query = sessionFactory.getCurrentSession().createNativeQuery(
				"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"
			);

			query.setParameter("chunk", CHUNKS);
			try {
				for (Object row : query.list()) {
					Class<?> eventClass = Class.forName(row.toString());
					DomainEvent event = (DomainEvent) eventClass.getConstructor().newInstance();
					dispatch(event);
				}
			} catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException |
					 InstantiationException e) {
				e.printStackTrace();
			}
			break;
		}
	}

	private void dispatch(DomainEvent event) {
	}
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/application/enroll/BulkEnrollmentService.java",
        """package tv.codely.mooc.courses.application.enroll;

import java.util.List;
import tv.codely.mooc.courses.domain.CourseId;
import tv.codely.mooc.courses.domain.CourseEnrollment;
import tv.codely.mooc.courses.domain.CourseEnrollmentEvent;
import tv.codely.mooc.courses.domain.CourseEnrollmentRepository;
import tv.codely.mooc.courses.domain.CourseLockRegistry;
import tv.codely.mooc.courses.domain.StudentId;
import tv.codely.shared.domain.bus.event.EventBus;

public final class BulkEnrollmentService {
    private final CourseEnrollmentRepository repository;
    private final CourseLockRegistry lockRegistry;
    private final EventBus eventBus;

    public BulkEnrollmentService(
        CourseEnrollmentRepository repository,
        CourseLockRegistry lockRegistry,
        EventBus eventBus
    ) {
        this.repository = repository;
        this.lockRegistry = lockRegistry;
        this.eventBus = eventBus;
    }

    public void enrollBatch(CourseId courseId, List<StudentId> studentIds) {
        Object lock = lockRegistry.lockFor(courseId.value());
        synchronized (lock) {
            if (studentIds.isEmpty()) {
                return;
            }
            List<CourseEnrollment> enrollments = studentIds.stream()
                .map(studentId -> CourseEnrollment.create(courseId, studentId))
                .toList();
            repository.saveAll(enrollments);
            eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));
        }
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/application/payment/PaymentSettlementService.java",
        """package tv.codely.mooc.courses.application.payment;

import java.util.List;
import org.springframework.data.domain.PageRequest;
import org.springframework.transaction.annotation.Transactional;
import tv.codely.mooc.courses.domain.CourseId;
import tv.codely.mooc.courses.domain.Payment;
import tv.codely.mooc.courses.domain.PaymentGateway;
import tv.codely.mooc.courses.domain.PaymentRepository;
import tv.codely.mooc.courses.domain.SettlementResult;

public final class PaymentSettlementService {
    private final PaymentRepository paymentRepository;
    private final PaymentGateway gateway;

    public PaymentSettlementService(PaymentRepository paymentRepository, PaymentGateway gateway) {
        this.paymentRepository = paymentRepository;
        this.gateway = gateway;
    }

    @Transactional
    public SettlementResult settle(CourseId courseId) {
        List<Payment> payments = paymentRepository.findPendingByCourse(courseId, PageRequest.of(0, 200));
        for (Payment payment : payments) {
            gateway.capture(payment);
            payment.markCaptured();
        }
        paymentRepository.saveAll(payments);
        return SettlementResult.success(payments.size());
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/CourseEnrollment.java",
        """package tv.codely.mooc.courses.domain;

public final class CourseEnrollment {
    public static CourseEnrollment create(CourseId courseId, StudentId studentId) {
        return new CourseEnrollment();
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/CourseEnrollmentEvent.java",
        """package tv.codely.mooc.courses.domain;

import java.util.List;
import tv.codely.shared.domain.bus.event.DomainEvent;

public final class CourseEnrollmentEvent implements DomainEvent {
    public static List<DomainEvent> batchCreated(CourseId courseId, int count) {
        return List.of(new CourseEnrollmentEvent());
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/CourseEnrollmentRepository.java",
        """package tv.codely.mooc.courses.domain;

import java.util.List;

public interface CourseEnrollmentRepository {
    void save(CourseEnrollment enrollment);
    void saveAll(List<CourseEnrollment> enrollments);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/CourseLockRegistry.java",
        """package tv.codely.mooc.courses.domain;

public interface CourseLockRegistry {
    Object lockFor(String courseId);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/StudentId.java",
        """package tv.codely.mooc.courses.domain;

public final class StudentId {
    private final String value;

    public StudentId(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/Payment.java",
        """package tv.codely.mooc.courses.domain;

public final class Payment {
    public void markCaptured() {
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/PaymentGateway.java",
        """package tv.codely.mooc.courses.domain;

public interface PaymentGateway {
    void capture(Payment payment);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/PaymentRepository.java",
        """package tv.codely.mooc.courses.domain;

import java.util.List;
import org.springframework.data.domain.PageRequest;

public interface PaymentRepository {
    List<Payment> findPendingByCourse(CourseId courseId, PageRequest pageRequest);
    List<Payment> searchPendingByCourseLike(String courseId);
    void save(Payment payment);
    void saveAll(List<Payment> payments);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/domain/SettlementResult.java",
        """package tv.codely.mooc.courses.domain;

public final class SettlementResult {
    public static SettlementResult success(int count) {
        return new SettlementResult();
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/mq/CourseEnrollmentConsumer.java",
        """package tv.codely.mooc.courses.infrastructure.mq;

import tv.codely.mooc.courses.domain.CourseEnrollmentRepository;
import tv.codely.mooc.courses.domain.StudentId;

public final class CourseEnrollmentConsumer {
    private final CourseEnrollmentRepository repository;
    private final ProcessedMessageRepository processedMessages;

    public CourseEnrollmentConsumer(
        CourseEnrollmentRepository repository,
        ProcessedMessageRepository processedMessages
    ) {
        this.repository = repository;
        this.processedMessages = processedMessages;
    }

    public void consume(EnrollmentMessage message, Acknowledgement acknowledgement) {
        if (processedMessages.exists(message.messageId())) {
            acknowledgement.ack();
            return;
        }
        repository.save(message.toEnrollment(new StudentId(message.studentId())));
        processedMessages.markProcessed(message.messageId());
        acknowledgement.ack();
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/mq/EnrollmentMessage.java",
        """package tv.codely.mooc.courses.infrastructure.mq;

import tv.codely.mooc.courses.domain.CourseEnrollment;
import tv.codely.mooc.courses.domain.CourseId;
import tv.codely.mooc.courses.domain.StudentId;

public final class EnrollmentMessage {
    public String messageId() {
        return "message-1";
    }

    public String studentId() {
        return "student-1";
    }

    public CourseEnrollment toEnrollment(StudentId studentId) {
        return CourseEnrollment.create(new CourseId("course-1"), studentId);
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/mq/Acknowledgement.java",
        """package tv.codely.mooc.courses.infrastructure.mq;

public interface Acknowledgement {
    void ack();
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/mq/ProcessedMessageRepository.java",
        """package tv.codely.mooc.courses.infrastructure.mq;

public interface ProcessedMessageRepository {
    boolean exists(String messageId);
    void markProcessed(String messageId);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/cache/CourseCacheWarmer.java",
        """package tv.codely.mooc.courses.infrastructure.cache;

import java.time.Duration;
import java.util.List;
import tv.codely.mooc.courses.domain.CourseId;

public final class CourseCacheWarmer {
    private final RedisClient redisClient;
    private final CourseReadRepository repository;

    public CourseCacheWarmer(RedisClient redisClient, CourseReadRepository repository) {
        this.redisClient = redisClient;
        this.repository = repository;
    }

    public void warmPopularCourses(List<CourseId> courseIds) {
        for (CourseId courseId : courseIds) {
            redisClient.set("course:" + courseId.value(), repository.snapshot(courseId), Duration.ofMinutes(30));
        }
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/cache/RedisClient.java",
        """package tv.codely.mooc.courses.infrastructure.cache;

import java.time.Duration;

public interface RedisClient {
    void set(String key, String value, Duration ttl);
    void set(String key, String value);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/main/tv/codely/mooc/courses/infrastructure/cache/CourseReadRepository.java",
        """package tv.codely.mooc.courses.infrastructure.cache;

import tv.codely.mooc.courses.domain.CourseId;

public interface CourseReadRepository {
    String snapshot(CourseId courseId);
}
""",
    )
    _write_fixture_file(
        repo_path,
        "src/mooc/test/tv/codely/mooc/courses/application/payment/PaymentSettlementServiceTest.java",
        """package tv.codely.mooc.courses.application.payment;

public final class PaymentSettlementServiceTest {
    public void settlesOnlyFirstPage() {
        // protects paginated settlement behavior
    }
}
""",
    )
    _write_fixture_file(
        repo_path,
        FIXTURE_MARKER_FILE,
        json.dumps(
            {
                "repo_key": "java-ddd-example",
                "purpose": "local benchmark fixture for review quality smoke tests",
                "version": FIXTURE_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    _run_git(["git", "init"], cwd=repo_path)
    _run_git(["git", "checkout", "-B", "main"], cwd=repo_path)
    _run_git(["git", "add", "."], cwd=repo_path)
    _run_git(
        [
            "git",
            "-c",
            "user.name=Codex",
            "-c",
            "user.email=codex@example.com",
            "commit",
            "-m",
            "init java ddd fixture",
        ],
        cwd=repo_path,
    )


def ensure_repo_cache(repository: RepoDefinition, cache_root: Path = DEFAULT_CACHE_ROOT) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / repository.repo_key
    seed = Path(repository.preferred_local_path) if repository.preferred_local_path else None
    fixture_enabled = str(os.getenv("JAVA_REVIEW_BENCH_USE_FIXTURE") or "").strip().lower() in {"1", "true", "yes", "on"}
    if (
        fixture_enabled
        and
        repository.repo_key == "java-ddd-example"
        and seed
        and (not _is_valid_git_repo(seed) or not _is_current_fixture_repo(seed))
    ):
        _create_java_ddd_example_fixture_repo(seed)
    fixture_seed_ready = (
        repository.repo_key == "java-ddd-example"
        and seed is not None
        and _is_valid_git_repo(seed)
        and _is_current_fixture_repo(seed)
    )
    fixture_cache_ready = _is_current_fixture_repo(cache_path)
    real_seed_ready = bool(
        repository.repo_key == "java-ddd-example"
        and seed is not None
        and _is_valid_git_repo(seed)
        and not _is_current_fixture_repo(seed)
    )
    if _is_valid_git_repo(cache_path) and not (
        (fixture_seed_ready and not fixture_cache_ready)
        or (real_seed_ready and fixture_cache_ready)
    ):
        return cache_path
    if cache_path.exists():
        shutil.rmtree(cache_path)
    if fixture_enabled and repository.repo_key == "java-ddd-example" and not (seed and _is_valid_git_repo(seed)):
        _create_java_ddd_example_fixture_repo(cache_path)
        return cache_path
    source = str(seed) if seed and _is_valid_git_repo(seed) else repository.clone_url
    try:
        _run_git(["git", "clone", "--quiet", source, str(cache_path)])
    except subprocess.CalledProcessError:
        if repository.repo_key != "java-ddd-example":
            raise
        _create_java_ddd_example_fixture_repo(cache_path)
    return cache_path


def _ensure_backend_import_path() -> None:
    backend_path = str(REPO_ROOT / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


def build_tree_sitter_graph(repo_path: Path, repository_id: str) -> dict[str, object]:
    graph_db_path = repo_path / ".code-review-graph" / "graph.db"
    try:
        _ensure_backend_import_path()
        from app.services.code_graph.index_service import CodeGraphIndexService
        from app.services.code_graph.java_tree_sitter_parser import JavaTreeSitterParser
    except Exception as error:
        return {
            "status": "skipped",
            "message": f"Tree-sitter 建图依赖不可用：{error}",
            "graph_db_path": str(graph_db_path),
            "error_type": error.__class__.__name__,
        }
    try:
        result = CodeGraphIndexService(repo_root=repo_path, parser=JavaTreeSitterParser()).full_build(
            repository_id=repository_id,
            languages=["java"],
        )
        return {"status": "ready", **dict(result), "graph_db_path": str(graph_db_path)}
    except Exception as error:
        return {
            "status": "failed",
            "message": f"Tree-sitter 建图失败：{error}",
            "graph_db_path": str(graph_db_path),
            "error_type": error.__class__.__name__,
        }


def _gitnexus_analyze_command() -> list[str]:
    raw = str(os.getenv("GITNEXUS_ANALYZE_COMMAND") or "").strip()
    if raw:
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list) and all(str(item).strip() for item in parsed):
                return [str(item) for item in parsed]
        parsed = shlex.split(raw)
        if parsed:
            return parsed
    command = shutil.which("gitnexus")
    return [command, "analyze"] if command else []


def build_gitnexus_graph(repo_path: Path, repository_id: str) -> dict[str, object]:
    graph_dir = repo_path / ".gitnexus"
    build_mode = str(os.getenv("JAVA_REVIEW_BENCH_BUILD_GITNEXUS") or "auto").strip().lower()
    if build_mode in {"0", "false", "no", "off"}:
        return {
            "status": "skipped",
            "message": "JAVA_REVIEW_BENCH_BUILD_GITNEXUS 已关闭，跳过 GitNexus 预建图。",
            "graph_dir": str(graph_dir),
        }
    if build_mode == "auto" and repository_id != "java-ddd-example":
        return {
            "status": "skipped",
            "message": "auto 模式仅为 java-ddd-example 本地模拟仓预建 GitNexus 图谱。",
            "graph_dir": str(graph_dir),
        }
    command = _gitnexus_analyze_command()
    if not command:
        return {
            "status": "skipped",
            "message": "GitNexus 命令不可用，跳过本地模拟仓预建图。",
            "graph_dir": str(graph_dir),
        }
    timeout_seconds = int(os.getenv("JAVA_REVIEW_BENCH_GITNEXUS_TIMEOUT_SECONDS", "180") or 180)
    try:
        completed = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as error:
        return {
            "status": "failed",
            "message": f"GitNexus 建图异常：{error}",
            "graph_dir": str(graph_dir),
            "command": " ".join(command),
            "timeout_seconds": timeout_seconds,
            "error_type": error.__class__.__name__,
        }
    graph_ready = (graph_dir / "meta.json").exists() or (graph_dir / "index_status.json").exists()
    return {
        "status": "ready" if completed.returncode == 0 and graph_ready else "failed",
        "message": "GitNexus 本地模拟仓图谱已创建。" if completed.returncode == 0 and graph_ready else "GitNexus 建图未生成图谱文件。",
        "graph_dir": str(graph_dir),
        "command": " ".join(command),
        "return_code": completed.returncode,
        "timeout_seconds": timeout_seconds,
        "stdout_tail": (completed.stdout or "")[-1200:],
        "stderr_tail": (completed.stderr or "")[-1200:],
    }


def build_local_graph_metadata(repo_path: Path, repository_id: str) -> dict[str, object]:
    tree_sitter_result = build_tree_sitter_graph(repo_path, repository_id)
    gitnexus_result = build_gitnexus_graph(repo_path, repository_id)
    metadata: dict[str, object] = {
        "local_graph_build": {
            "tree_sitter": tree_sitter_result,
            "gitnexus": gitnexus_result,
        },
        "tree_sitter_graph_result": tree_sitter_result,
        "gitnexus_graph_result": gitnexus_result,
    }
    graph_db_path = str(tree_sitter_result.get("graph_db_path") or "").strip()
    if graph_db_path:
        metadata["code_graph_db_path"] = graph_db_path
    graph_dir = str(gitnexus_result.get("graph_dir") or "").strip()
    if graph_dir:
        metadata["gitnexus_graph_dir"] = graph_dir
    return metadata


def _apply_patch_operations(repo_dir: Path, operations: tuple[PatchOperation, ...]) -> tuple[str, ...]:
    changed_files: list[str] = []
    for operation in operations:
        file_path = repo_dir / operation.path
        original = file_path.read_text(encoding="utf-8")
        if operation.search not in original:
            raise ValueError(f"patch search snippet not found for {operation.path}")
        updated = original.replace(operation.search, operation.replace, 1)
        if updated == original:
            raise ValueError(f"patch operation produced no change for {operation.path}")
        file_path.write_text(updated, encoding="utf-8")
        if operation.path not in changed_files:
            changed_files.append(operation.path)
    return tuple(changed_files)


def _patch_operations_available(repo_dir: Path, operations: tuple[PatchOperation, ...]) -> bool:
    for operation in operations:
        file_path = repo_dir / operation.path
        if not file_path.exists() or not file_path.is_file():
            return False
        try:
            original = file_path.read_text(encoding="utf-8")
        except OSError:
            return False
        if operation.search not in original:
            return False
    return True


def build_git_diff(repo_dir: Path, changed_files: tuple[str, ...]) -> str:
    if not changed_files:
        raise ValueError("changed_files cannot be empty")
    result = _run_git(["git", "-C", str(repo_dir), "diff", "--", *changed_files])
    diff = result.stdout
    if not diff.strip():
        raise ValueError("generated diff is empty")
    return diff


def materialize_case(
    case: JavaReviewCase,
    repositories: dict[str, RepoDefinition],
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
) -> MaterializedCase:
    repository = repositories[case.repo_key]
    source_repo = ensure_repo_cache(repository, cache_root)
    if repository.repo_key == "java-ddd-example" and not _patch_operations_available(source_repo, case.patch_operations):
        fixture_repo = cache_root / repository.repo_key
        _create_java_ddd_example_fixture_repo(fixture_repo)
        source_repo = fixture_repo
    workspace_root.mkdir(parents=True, exist_ok=True)
    target_repo = workspace_root / case.case_id
    if target_repo.exists():
        shutil.rmtree(target_repo)
    _run_git(["git", "clone", "--quiet", str(source_repo), str(target_repo)])
    changed_files = _apply_patch_operations(target_repo, case.patch_operations)
    unified_diff = build_git_diff(target_repo, changed_files)
    graph_metadata = build_local_graph_metadata(target_repo, repository.repo_key)
    return MaterializedCase(
        case=case,
        repository=repository,
        workspace_repo=target_repo,
        changed_files=changed_files,
        unified_diff=unified_diff,
        graph_metadata=graph_metadata,
    )


def request_json(method: str, url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def submit_case(
    materialized: MaterializedCase,
    api_base: str = DEFAULT_API_BASE,
    analysis_mode: str = "light",
    wait_timeout_seconds: int = 900,
    poll_interval_seconds: int = 5,
    windows_quality_gate: bool = False,
    quality_gate_model: str = "MiniMax-M2.7",
) -> dict[str, object]:
    created = request_json("POST", f"{api_base}/reviews", materialized.to_review_payload(analysis_mode=analysis_mode))
    review_id = str(created["review_id"])
    request_json("POST", f"{api_base}/reviews/{review_id}/start")
    deadline = time.time() + wait_timeout_seconds
    latest_review: dict[str, object] = {}
    while time.time() < deadline:
        latest_review = request_json("GET", f"{api_base}/reviews/{review_id}")
        status = str(latest_review.get("status") or "")
        if status in {"completed", "failed", "closed", "waiting_human"}:
            break
        time.sleep(poll_interval_seconds)
    report = request_json("GET", f"{api_base}/reviews/{review_id}/report")
    replay = request_json("GET", f"{api_base}/reviews/{review_id}/replay")
    findings = report.get("findings", []) if isinstance(report, dict) else []
    issues = report.get("issues", []) if isinstance(report, dict) else []
    issue_filter_decisions = report.get("issue_filter_decisions", []) if isinstance(report, dict) else []
    filtered_rule_codes = [
        str(item.get("rule_code") or "")
        for item in issue_filter_decisions
        if isinstance(item, dict) and str(item.get("rule_code") or "").strip()
    ]
    review_status = str(latest_review.get("status") or "")
    review_phase = str(latest_review.get("phase") or "")
    score = evaluate_case_result(materialized.case, report if isinstance(report, dict) else {}, replay if isinstance(replay, dict) else {})
    quality_eval = evaluate_quality_case(
        _quality_eval_case_from_benchmark(materialized.case),
        _final_issue_report_for_quality_eval(report if isinstance(report, dict) else {}),
    )
    windows_quality_report: dict[str, object] | None = None
    if windows_quality_gate:
        windows_quality_report = build_windows_review_quality_report(
            workspace_path=str(materialized.workspace_repo),
            changed_files=list(materialized.changed_files),
            metadata=dict(materialized.graph_metadata or {}),
            report=report if isinstance(report, dict) else {},
            replay=replay if isinstance(replay, dict) else {},
            model_name=quality_gate_model,
        )
    if review_status not in {"completed", "failed", "closed", "waiting_human"}:
        score = replace(
            score,
            incomplete=True,
            passed=False,
            review_status=review_status,
            review_phase=review_phase,
        )
    result = {
        "case_id": materialized.case.case_id,
        "review_id": review_id,
        "status": latest_review.get("status", ""),
        "phase": latest_review.get("phase", ""),
        "finding_count": len(findings) if isinstance(findings, list) else 0,
        "issue_count": len(issues) if isinstance(issues, list) else 0,
        "filtered_count": sum(
            len(item.get("finding_ids") or [])
            for item in issue_filter_decisions
            if isinstance(item, dict)
        ),
        "filtered_rule_codes": filtered_rule_codes,
        "matched_issue_titles": [
            str(item.get("title") or "")
            for item in issues
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ],
        "matched_issue_primary_experts": [
            str(item.get("primary_expert_id") or "")
            for item in issues
            if isinstance(item, dict) and str(item.get("primary_expert_id") or "").strip()
        ],
        "matched_finding_titles": [
            str(item.get("title") or "")
            for item in findings
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ],
        "matched_finding_experts": [
            str(item.get("expert_id") or "")
            for item in findings
            if isinstance(item, dict) and str(item.get("expert_id") or "").strip()
        ],
        "replay_message_count": len(replay.get("messages", [])) if isinstance(replay, dict) else 0,
        "score": {
            "passed": score.passed,
            "score": score.score,
            "required_expert_coverage": score.required_expert_coverage,
            "required_rule_hit": score.required_rule_hit,
            "finding_keyword_coverage": score.finding_keyword_coverage,
            "input_quality_coverage": score.input_quality_coverage,
            "problem_marker_coverage": score.problem_marker_coverage,
            "invalid_finding_rate": score.invalid_finding_rate,
            "schema_valid_rate": score.schema_valid_rate,
            "rule_coverage_rate": score.rule_coverage_rate,
            "context_hit_rate": score.context_hit_rate,
            "timeout_rate": score.timeout_rate,
            "missing_experts": list(score.missing_experts),
            "matched_rule_ids": list(score.matched_rule_ids),
            "missing_keywords": list(score.missing_keywords),
            "missing_problem_markers": list(score.missing_problem_markers),
            "missing_input_sections": list(score.missing_input_sections),
            "incomplete": score.incomplete,
            "review_status": score.review_status,
            "review_phase": score.review_phase,
        },
        "quality_eval": {
            "required_recall": quality_eval["required_recall"],
            "critical_recall": quality_eval["critical_recall"],
            "precision": quality_eval["precision"],
            "blocking_precision": quality_eval["blocking_precision"],
            "anchor_accuracy": quality_eval["anchor_accuracy"],
            "display_quality_rate": quality_eval["display_quality_rate"],
            "duplicate_rate": quality_eval["duplicate_rate"],
            "false_positive_rate": quality_eval["false_positive_rate"],
            "runtime_seconds_per_review": quality_eval["runtime_seconds_per_review"],
            "missing_required": list(quality_eval["missing_required"]),
            "matched_expected_ids": list(quality_eval["matched_expected_ids"]),
        },
        "score_summary": _build_score_summary(score),
    }
    if windows_quality_report is not None:
        result["windows_quality_gate"] = {
            "passed": bool(windows_quality_report.get("passed")),
            "missing": list(windows_quality_report.get("missing") or []),
            "executed_experts": list(windows_quality_report.get("executed_experts") or []),
            "issue_count": int(windows_quality_report.get("issue_count") or 0),
            "finding_count": int(windows_quality_report.get("finding_count") or 0),
            "model_name": str(windows_quality_report.get("model_name") or quality_gate_model),
            "prompt_profile": str(windows_quality_report.get("prompt_profile") or ""),
        }
    return result


def _collect_matched_rule_ids(findings: list[dict[str, object]], replay_messages: list[dict[str, object]]) -> tuple[str, ...]:
    matched_rule_ids: list[str] = []
    for finding in findings:
        for rule in list(finding.get("matched_rules") or []):
            rule_id = str(rule or "").strip()
            if rule_id and rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
    for message in replay_messages:
        metadata = message.get("metadata") if isinstance(message, dict) else {}
        if not isinstance(metadata, dict):
            continue
        for rule in list(metadata.get("matched_rules") or []):
            rule_id = str(rule or "").strip()
            if rule_id and rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
        for key in ("rule_check_results", "candidate_findings"):
            for item in list(metadata.get(key) or []):
                if not isinstance(item, dict):
                    continue
                rule_id = str(item.get("rule_id") or "").strip()
                if rule_id and rule_id not in matched_rule_ids:
                    matched_rule_ids.append(rule_id)
        rule_screening = metadata.get("rule_screening")
        if not isinstance(rule_screening, dict):
            continue
        for item in list(rule_screening.get("matched_rules_for_llm") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            if rule_id and rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
    return tuple(matched_rule_ids)


def _collect_executed_experts(findings: list[dict[str, object]], replay_messages: list[dict[str, object]]) -> tuple[str, ...]:
    expert_ids: list[str] = []
    for finding in findings:
        expert_id = str(finding.get("expert_id") or "").strip()
        if expert_id and expert_id not in expert_ids:
            expert_ids.append(expert_id)
    for message in replay_messages:
        if not isinstance(message, dict):
            continue
        expert_id = str(message.get("expert_id") or "").strip()
        message_type = str(message.get("message_type") or "").strip()
        if expert_id and expert_id not in {"main_agent", "judge"} and message_type in {"expert_analysis", "expert_ack", "expert_tool_call"}:
            if expert_id not in expert_ids:
                expert_ids.append(expert_id)
    return tuple(expert_ids)


def _collect_input_quality(findings: list[dict[str, object]], replay_messages: list[dict[str, object]]) -> tuple[float, tuple[str, ...]]:
    coverage_values: list[float] = []
    missing_sections: list[str] = []
    payloads: list[dict[str, object]] = []
    for finding in findings:
        code_context = finding.get("code_context")
        if isinstance(code_context, dict):
            input_completeness = code_context.get("input_completeness")
            if isinstance(input_completeness, dict):
                payloads.append(input_completeness)
    for message in replay_messages:
        metadata = message.get("metadata") if isinstance(message, dict) else {}
        if not isinstance(metadata, dict):
            continue
        input_completeness = metadata.get("input_completeness")
        if isinstance(input_completeness, dict):
            payloads.append(input_completeness)
    for payload in payloads:
        derived_missing: list[str] = []
        checks = [
            bool(payload.get("review_spec_present")),
            bool(payload.get("language_guidance_present")),
            bool(payload.get("target_file_diff_present")),
            bool(payload.get("source_context_present")),
            int(payload.get("related_context_count") or 0) > 0,
        ]
        if not checks[0]:
            derived_missing.append("专家规范")
        if not checks[1]:
            derived_missing.append("语言通用规范提示")
        if not checks[2]:
            derived_missing.append("变更代码原文")
        if not checks[3]:
            derived_missing.append("当前源码上下文")
        if not checks[4]:
            derived_missing.append("关联源码上下文")
        coverage_values.append(sum(1 for item in checks if item) / len(checks))
        for section in derived_missing:
            text = str(section).strip()
            if text and text not in missing_sections:
                missing_sections.append(text)
    average = sum(coverage_values) / len(coverage_values) if coverage_values else 1.0
    return average, tuple(missing_sections)


def _keyword_matches(haystack: str, keyword: str) -> bool:
    lowered_keyword = str(keyword or "").strip().lower()
    if not lowered_keyword:
        return True
    aliases = KEYWORD_ALIASES.get(lowered_keyword, (lowered_keyword,))
    return any(str(alias or "").strip().lower() in haystack for alias in aliases if str(alias or "").strip())


def _collect_invalid_finding_rate(findings: list[dict[str, object]], issues: list[dict[str, object]]) -> float:
    invalid_tokens = {
        "请用户",
        "用户确认",
        "用户核查",
        "用户查看",
        "人工确认",
        "人工核查",
        "人工查看",
        "自行确认",
        "自行核实",
        "建议查看",
        "需要查看",
        "请查看",
        "需要核对",
        "建议核对",
        "完整方法/类定义后再确认",
        "回看完整 diff",
    }
    payloads = [*findings, *issues]
    if not payloads:
        return 0.0
    invalid_count = 0
    for item in payloads:
        blob = "\n".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("rule_based_reasoning") or ""),
                str(item.get("verification_plan") or ""),
                str(item.get("remediation_suggestion") or ""),
                *[str(value or "") for value in list(item.get("assumptions") or [])],
                *[str(value or "") for value in list(item.get("change_steps") or [])],
                *[str(value or "") for value in list(item.get("aggregated_summaries") or [])],
            ]
        )
        if any(token in blob for token in invalid_tokens):
            invalid_count += 1
    return invalid_count / len(payloads)


def _collect_problem_marker_coverage(
    expected_markers: tuple[dict[str, object], ...],
    findings: list[dict[str, object]],
    issues: list[dict[str, object]],
) -> tuple[float, tuple[str, ...]]:
    if not expected_markers:
        return 1.0, ()

    payloads = [*findings, *issues]
    missing_markers: list[str] = []

    def _payload_blob(item: dict[str, object]) -> str:
        return "\n".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("rule_based_reasoning") or ""),
                str(item.get("verification_plan") or ""),
                str(item.get("remediation_suggestion") or ""),
                *[str(value or "") for value in list(item.get("aggregated_titles") or [])],
                *[str(value or "") for value in list(item.get("aggregated_summaries") or [])],
            ]
        ).lower()

    for marker in expected_markers:
        file_path = str(marker.get("file_path") or "").strip()
        keywords = tuple(str(keyword).strip().lower() for keyword in marker.get("keywords", []) if str(keyword).strip())
        matched = False
        for item in payloads:
            if str(item.get("file_path") or "").strip() != file_path:
                continue
            blob = _payload_blob(item)
            if all(_keyword_matches(blob, keyword) for keyword in keywords):
                matched = True
                break
        if not matched:
            label = f"{Path(file_path).name}:{'&'.join(keywords[:3])}" if keywords else Path(file_path).name
            if label not in missing_markers:
                missing_markers.append(label)

    coverage = (len(expected_markers) - len(missing_markers)) / len(expected_markers)
    return coverage, tuple(missing_markers)


def _collect_quality_gate_metrics(replay_messages: list[dict[str, object]]) -> dict[str, float]:
    schema_checks = 0
    schema_valid = 0
    rule_coverage_values: list[float] = []
    context_hit_values: list[float] = []
    timeout_checks = 0
    timeout_count = 0
    for message in replay_messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("message_type") or "")
        content = str(message.get("content") or "")
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        if message_type in {"expert_analysis", "expert_final"} or metadata.get("prompt_snapshot_summary"):
            schema_checks += 1
            schema_errors = list(metadata.get("schema_errors") or [])
            schema_contract = metadata.get("schema_contract") if isinstance(metadata.get("schema_contract"), dict) else {}
            repair = schema_contract.get("context_followup") if isinstance(schema_contract.get("context_followup"), dict) else {}
            if not schema_errors and not (
                schema_contract
                and schema_contract.get("initial_valid") is False
                and schema_contract.get("repair_success") is False
            ) and not (repair and repair.get("success") is False):
                schema_valid += 1
        rule_coverage = metadata.get("rule_coverage")
        if isinstance(rule_coverage, dict):
            matched = int(rule_coverage.get("matched_rule_count") or 0)
            checked = int(rule_coverage.get("checked_rule_count") or 0)
            if matched > 0:
                rule_coverage_values.append(min(1.0, checked / matched))
        input_completeness = metadata.get("input_completeness")
        if isinstance(input_completeness, dict):
            context_hit_values.append(
                (
                    (1.0 if bool(input_completeness.get("source_context_present")) else 0.0)
                    + (1.0 if int(input_completeness.get("related_context_count") or 0) > 0 else 0.0)
                )
                / 2.0
            )
        if message_type.startswith("expert") or metadata.get("llm_error"):
            timeout_checks += 1
            blob = f"{content}\n{metadata.get('llm_error') or metadata.get('error') or ''}".lower()
            if "timeout" in blob or "timed out" in blob:
                timeout_count += 1
    return {
        "schema_valid_rate": round(schema_valid / schema_checks, 3) if schema_checks else 1.0,
        "rule_coverage_rate": round(sum(rule_coverage_values) / len(rule_coverage_values), 3)
        if rule_coverage_values
        else 1.0,
        "context_hit_rate": round(sum(context_hit_values) / len(context_hit_values), 3)
        if context_hit_values
        else 1.0,
        "timeout_rate": round(timeout_count / timeout_checks, 3) if timeout_checks else 0.0,
    }


def evaluate_case_result(case: JavaReviewCase, report: dict[str, object], replay: dict[str, object]) -> BenchmarkScore:
    findings = [item for item in list(report.get("findings") or []) if isinstance(item, dict)]
    issues = [item for item in list(report.get("issues") or []) if isinstance(item, dict)]
    replay_messages = [item for item in list(replay.get("messages") or []) if isinstance(item, dict)]

    executed_experts = _collect_executed_experts(findings, replay_messages)
    matched_rule_ids = _collect_matched_rule_ids(findings, replay_messages)
    input_quality_coverage, missing_input_sections = _collect_input_quality(findings, replay_messages)
    invalid_finding_rate = _collect_invalid_finding_rate(findings, issues)
    quality_metrics = _collect_quality_gate_metrics(replay_messages)
    problem_marker_coverage, missing_problem_markers = _collect_problem_marker_coverage(
        case.expected.problem_markers,
        findings,
        issues,
    )

    required_experts = tuple(case.expected.required_experts)
    missing_experts = tuple(expert for expert in required_experts if expert not in executed_experts)
    required_expert_coverage = (
        (len(required_experts) - len(missing_experts)) / len(required_experts) if required_experts else 1.0
    )

    required_rule_hit = any(rule_id in matched_rule_ids for rule_id in case.expected.rule_ids_any_of) if case.expected.rule_ids_any_of else True

    text_blobs: list[str] = []
    for finding in findings:
        text_blobs.extend(
            [
                str(finding.get("title") or ""),
                str(finding.get("summary") or ""),
                " ".join(str(item or "") for item in list(finding.get("matched_rules") or [])),
                " ".join(str(item or "") for item in list(finding.get("violated_guidelines") or [])),
            ]
        )
    for issue in issues:
        text_blobs.extend([str(issue.get("title") or ""), str(issue.get("summary") or "")])
    haystack = "\n".join(text_blobs).lower()
    missing_keywords = tuple(keyword for keyword in case.expected.finding_keywords if not _keyword_matches(haystack, keyword))
    finding_keyword_coverage = (
        (len(case.expected.finding_keywords) - len(missing_keywords)) / len(case.expected.finding_keywords)
        if case.expected.finding_keywords
        else 1.0
    )

    min_findings_ok = len(findings) >= case.expected.min_findings
    min_issues_ok = len(issues) >= case.expected.min_issues
    passed = (
        min_findings_ok
        and min_issues_ok
        and required_expert_coverage >= 1.0
        and required_rule_hit
        and finding_keyword_coverage >= 0.5
        and problem_marker_coverage >= 0.7
        and input_quality_coverage >= 0.8
        and invalid_finding_rate <= 0.05
        and quality_metrics["schema_valid_rate"] >= 0.95
        and quality_metrics["rule_coverage_rate"] >= 0.8
        and quality_metrics["context_hit_rate"] >= 0.8
        and quality_metrics["timeout_rate"] <= 0.1
    )
    score = round(
        (
            required_expert_coverage * 0.25
            + (1.0 if required_rule_hit else 0.0) * 0.2
            + finding_keyword_coverage * 0.2
            + problem_marker_coverage * 0.15
            + input_quality_coverage * 0.15
            + max(0.0, 1.0 - invalid_finding_rate) * 0.05
        ),
        3,
    )
    return BenchmarkScore(
        passed=passed,
        score=score,
        required_expert_coverage=round(required_expert_coverage, 3),
        required_rule_hit=required_rule_hit,
        finding_keyword_coverage=round(finding_keyword_coverage, 3),
        input_quality_coverage=round(input_quality_coverage, 3),
        problem_marker_coverage=round(problem_marker_coverage, 3),
        invalid_finding_rate=round(invalid_finding_rate, 3),
        missing_experts=missing_experts,
        matched_rule_ids=matched_rule_ids,
        missing_keywords=missing_keywords,
        missing_problem_markers=missing_problem_markers,
        missing_input_sections=missing_input_sections,
        schema_valid_rate=quality_metrics["schema_valid_rate"],
        rule_coverage_rate=quality_metrics["rule_coverage_rate"],
        context_hit_rate=quality_metrics["context_hit_rate"],
        timeout_rate=quality_metrics["timeout_rate"],
    )


def _serialise_case(case: JavaReviewCase) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "repo_key": case.repo_key,
        "category": case.category,
        "scenario": case.scenario,
        "tags": list(case.tags),
        "required_experts": list(case.expected.required_experts),
        "rule_ids_any_of": list(case.expected.rule_ids_any_of),
        "problem_markers": list(case.expected.problem_markers),
    }


def _serialise_materialized(materialized: MaterializedCase) -> dict[str, object]:
    return {
        "case_id": materialized.case.case_id,
        "repo_key": materialized.repository.repo_key,
        "workspace_repo": str(materialized.workspace_repo),
        "changed_files": list(materialized.changed_files),
        "diff_line_count": len(materialized.unified_diff.splitlines()),
        "expected_required_experts": list(materialized.case.expected.required_experts),
        "expected_rule_ids_any_of": list(materialized.case.expected.rule_ids_any_of),
        "expected_problem_markers": list(materialized.case.expected.problem_markers),
    }


def _benchmark_exit_code(results: list[dict[str, object]], *, windows_quality_gate: bool) -> int:
    if windows_quality_gate:
        for result in results:
            score = result.get("score")
            if isinstance(score, dict) and not bool(score.get("passed")):
                return 2
            gate = result.get("windows_quality_gate")
            if not isinstance(gate, dict) or not bool(gate.get("passed")):
                return 2
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally run realistic Java review benchmark cases.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--case", dest="case_ids", action="append", help="Run one or more case ids.")
    parser.add_argument("--list", action="store_true", help="List available case ids and exit.")
    parser.add_argument("--validate-coverage", action="store_true", help="Validate benchmark inventory covers required Java defect classes.")
    parser.add_argument("--prepare-only", action="store_true", help="Only materialize repo workspaces and unified diffs.")
    parser.add_argument("--submit", action="store_true", help="Create and start reviews through the local API.")
    parser.add_argument("--analysis-mode", default="light", choices=["light", "standard"])
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--wait-timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-interval-seconds", type=int, default=5)
    parser.add_argument(
        "--windows-quality-gate",
        action="store_true",
        help="After submission, run the Windows/MiniMax review quality gate against each real review result.",
    )
    parser.add_argument("--quality-gate-model", default="MiniMax-M2.7")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    repositories = load_repositories(manifest_path)
    cases = select_cases(load_cases(manifest_path), args.case_ids)

    if args.validate_coverage:
        result = validate_benchmark_problem_coverage(cases)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 2

    if args.list:
        print(json.dumps([_serialise_case(item) for item in cases], ensure_ascii=False, indent=2))
        return 0

    materialized_cases = [
        materialize_case(
            item,
            repositories,
            workspace_root=Path(args.workspace_root),
            cache_root=Path(args.cache_root),
        )
        for item in cases
    ]

    if args.prepare_only or not args.submit:
        print(json.dumps([_serialise_materialized(item) for item in materialized_cases], ensure_ascii=False, indent=2))
        return 0

    results = [
        submit_case(
            item,
            api_base=args.api_base,
            analysis_mode=args.analysis_mode,
            wait_timeout_seconds=args.wait_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            windows_quality_gate=bool(args.windows_quality_gate),
            quality_gate_model=args.quality_gate_model,
        )
        for item in materialized_cases
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return _benchmark_exit_code(results, windows_quality_gate=bool(args.windows_quality_gate))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, ValueError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(f"java benchmark failed: {error}")
        raise SystemExit(1)
