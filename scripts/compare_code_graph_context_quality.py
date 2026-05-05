from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.code_graph.context_planner import CodeGraphContextPlanner


@dataclass(frozen=True)
class ContextQualityCase:
    case_id: str
    title: str
    changed_files: tuple[str, ...]
    changed_symbols: tuple[str, ...]
    expected_files: tuple[str, ...]
    expected_terms: tuple[str, ...]
    files: dict[str, str]


class EmptyCodeGraphService:
    def search_related_context(
        self,
        *,
        repository_id: str,
        changed_files: list[str],
        changed_symbols: list[str],
        limit: int,
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    ) -> dict[str, object]:
        return {
            "ready": True,
            "contexts": [],
            "fallback_reason": "baseline_keyword_only",
            "stats": {
                "changed_node_count": len(changed_symbols),
                "context_count": 0,
            },
        }


class LocalSearchContext:
    def __init__(self, local_path: Path) -> None:
        self.local_path = local_path

    def is_searchable_path(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        ignored_parts = {".git", "target", "build", ".gradle", ".mvn"}
        return not any(part in ignored_parts for part in normalized.split("/"))

    def search_many(
        self,
        queries: list[str],
        globs: list[str] | None = None,
        limit_per_query: int = 4,
        total_limit: int = 12,
    ) -> dict[str, object]:
        suffixes = tuple(
            pattern.replace("*", "")
            for pattern in (globs or ["*.java", "*.xml", "*.sql"])
            if pattern.startswith("*.")
        )
        matches: list[dict[str, object]] = []
        seen: set[tuple[str, int, str]] = set()
        for raw_query in queries:
            query = raw_query.strip()
            if len(query) < 3:
                continue
            query_hits = 0
            for path in sorted(self.local_path.rglob("*")):
                if query_hits >= limit_per_query or len(matches) >= total_limit:
                    break
                if not path.is_file():
                    continue
                relative_path = path.relative_to(self.local_path).as_posix()
                if not self.is_searchable_path(relative_path):
                    continue
                if suffixes and not relative_path.endswith(suffixes):
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:
                    continue
                lowered_query = query.lower()
                for index, line in enumerate(lines, start=1):
                    if lowered_query not in line.lower():
                        continue
                    key = (relative_path, index, query)
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append(
                        {
                            "path": relative_path,
                            "line_number": index,
                            "source_query": query,
                            "snippet": _window(lines, index),
                            "score": 1.0,
                        }
                    )
                    query_hits += 1
                    break
                if len(matches) >= total_limit:
                    break
        return {"matches": matches}


def _window(lines: list[str], line_number: int, radius: int = 2) -> str:
    start = max(0, line_number - radius - 1)
    end = min(len(lines), line_number + radius)
    return "\n".join(lines[start:end])


def _cases() -> list[ContextQualityCase]:
    return [
        ContextQualityCase(
            case_id="validation_guard_removed",
            title="服务层创建订单时绕过命令校验",
            changed_files=("src/main/java/com/acme/order/OrderService.java",),
            changed_symbols=("OrderService", "create", "validate", "OrderCommand"),
            expected_files=(
                "src/main/java/com/acme/order/OrderService.java",
                "src/main/java/com/acme/order/OrderController.java",
                "src/test/java/com/acme/order/OrderServiceTest.java",
            ),
            expected_terms=("validate", "OrderCommand", "orderService.create"),
            files={
                "src/main/java/com/acme/order/OrderCommand.java": """
                    package com.acme.order;
                    public record OrderCommand(String userId, int quantity) {}
                """,
                "src/main/java/com/acme/order/OrderValidator.java": """
                    package com.acme.order;
                    public class OrderValidator {
                        public void validate(OrderCommand command) {
                            if (command.quantity() <= 0) {
                                throw new IllegalArgumentException("quantity must be positive");
                            }
                        }
                    }
                """,
                "src/main/java/com/acme/order/OrderService.java": """
                    package com.acme.order;
                    public class OrderService {
                        private final OrderValidator validator;
                        public OrderService(OrderValidator validator) {
                            this.validator = validator;
                        }
                        public Order create(OrderCommand command) {
                            return new Order(command.userId(), command.quantity());
                        }
                    }
                """,
                "src/main/java/com/acme/order/OrderController.java": """
                    package com.acme.order;
                    public class OrderController {
                        private final OrderService orderService;
                        public OrderController(OrderService orderService) {
                            this.orderService = orderService;
                        }
                        public Order submit(OrderCommand command) {
                            return orderService.create(command);
                        }
                    }
                """,
                "src/test/java/com/acme/order/OrderServiceTest.java": """
                    package com.acme.order;
                    public class OrderServiceTest {
                        void rejectsNegativeQuantity(OrderService orderService) {
                            orderService.create(new OrderCommand("u1", -1));
                        }
                    }
                """,
                "src/main/java/com/acme/order/OrderKeywordNoise.java": """
                    package com.acme.order;
                    public class OrderKeywordNoise {
                        // validate OrderCommand create OrderService are historical words in this comment.
                        private String notes = "validate create order command";
                    }
                """,
            },
        ),
        ContextQualityCase(
            case_id="ddd_factory_bypass",
            title="DDD 创建流程绕过聚合工厂与领域事件",
            changed_files=("src/main/java/com/acme/course/CourseCreator.java",),
            changed_symbols=("CourseCreator", "Course", "create", "CourseCreatedDomainEvent", "pullDomainEvents"),
            expected_files=(
                "src/main/java/com/acme/course/CourseCreator.java",
                "src/main/java/com/acme/course/Course.java",
                "src/main/java/com/acme/course/CourseCreatedDomainEvent.java",
            ),
            expected_terms=("Course.create", "pullDomainEvents", "CourseCreatedDomainEvent"),
            files={
                "src/main/java/com/acme/course/Course.java": """
                    package com.acme.course;
                    import java.util.ArrayList;
                    import java.util.List;
                    public class Course {
                        private final List<Object> events = new ArrayList<>();
                        private final String title;
                        private Course(String title) {
                            this.title = title;
                        }
                        public static Course create(String title) {
                            Course course = new Course(title);
                            course.events.add(new CourseCreatedDomainEvent(title));
                            return course;
                        }
                        public List<Object> pullDomainEvents() {
                            return List.copyOf(events);
                        }
                    }
                """,
                "src/main/java/com/acme/course/CourseCreatedDomainEvent.java": """
                    package com.acme.course;
                    public record CourseCreatedDomainEvent(String title) {}
                """,
                "src/main/java/com/acme/course/CourseCreator.java": """
                    package com.acme.course;
                    public class CourseCreator {
                        private final CourseRepository repository;
                        private final EventBus eventBus;
                        public CourseCreator(CourseRepository repository, EventBus eventBus) {
                            this.repository = repository;
                            this.eventBus = eventBus;
                        }
                        public Course create(String title) {
                            Course course = new Course(title);
                            repository.save(course);
                            return course;
                        }
                    }
                """,
                "src/main/java/com/acme/course/EventBus.java": """
                    package com.acme.course;
                    public interface EventBus {
                        void publish(Object event);
                    }
                """,
                "src/main/java/com/acme/course/CourseRepository.java": """
                    package com.acme.course;
                    public interface CourseRepository {
                        void save(Course course);
                    }
                """,
                "src/main/java/com/acme/course/CourseGlossary.java": """
                    package com.acme.course;
                    public class CourseGlossary {
                        // Course create CourseCreator pullDomainEvents CourseCreatedDomainEvent are glossary terms only.
                    }
                """,
            },
        ),
        ContextQualityCase(
            case_id="unbounded_query",
            title="列表查询从分页退化为无界查询",
            changed_files=("src/main/java/com/acme/owner/OwnerController.java",),
            changed_symbols=("OwnerController", "search", "findAllByStatus", "Pageable", "OwnerRepository"),
            expected_files=(
                "src/main/java/com/acme/owner/OwnerController.java",
                "src/main/java/com/acme/owner/OwnerRepository.java",
                "src/test/java/com/acme/owner/OwnerControllerTest.java",
            ),
            expected_terms=("findAllByStatus", "Pageable", "ownerRepository.findAllByStatus"),
            files={
                "src/main/java/com/acme/owner/OwnerRepository.java": """
                    package com.acme.owner;
                    import java.util.List;
                    public interface OwnerRepository {
                        Page<Owner> findPageByStatus(String status, Pageable pageable);
                        List<Owner> findAllByStatus(String status);
                    }
                """,
                "src/main/java/com/acme/owner/OwnerController.java": """
                    package com.acme.owner;
                    import java.util.List;
                    public class OwnerController {
                        private final OwnerRepository ownerRepository;
                        public OwnerController(OwnerRepository ownerRepository) {
                            this.ownerRepository = ownerRepository;
                        }
                        public List<Owner> search(String status, Pageable pageable) {
                            return ownerRepository.findAllByStatus(status);
                        }
                    }
                """,
                "src/test/java/com/acme/owner/OwnerControllerTest.java": """
                    package com.acme.owner;
                    public class OwnerControllerTest {
                        void keepsSearchBounded(OwnerController controller) {
                            controller.search("ACTIVE", PageRequest.of(0, 20));
                        }
                    }
                """,
                "src/main/java/com/acme/owner/OwnerQueryNotes.java": """
                    package com.acme.owner;
                    public class OwnerQueryNotes {
                        // OwnerController search findAllByStatus Pageable are mentioned in migration notes only.
                        String doc = "search Pageable OwnerRepository findAllByStatus";
                    }
                """,
            },
        ),
    ]


def _materialize(case: ContextQualityCase, root: Path) -> Path:
    repo_path = root / case.case_id
    if repo_path.exists():
        shutil.rmtree(repo_path)
    for relative_path, content in case.files.items():
        target = repo_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_normalize_java_source(content), encoding="utf-8")
    return repo_path


def _normalize_java_source(content: str) -> str:
    lines = content.strip("\n").splitlines()
    indentation = min((len(line) - len(line.lstrip(" ")) for line in lines if line.strip()), default=0)
    return "\n".join(line[indentation:] for line in lines).strip() + "\n"


def _run_planner(case: ContextQualityCase, repo_path: Path, *, mode: str, limit: int) -> dict[str, Any]:
    context = LocalSearchContext(repo_path)
    if mode == "tree_sitter":
        planner = CodeGraphContextPlanner()
    elif mode == "keyword":
        planner = CodeGraphContextPlanner(code_graph_service=EmptyCodeGraphService())
    else:
        raise ValueError(f"unsupported mode: {mode}")
    return planner.build_context_bundle(
        review_id=f"bench-{case.case_id}-{mode}",
        repository_id=case.case_id,
        changed_files=list(case.changed_files),
        changed_symbols=list(case.changed_symbols),
        repository_context_service=context,
        limit=limit,
    )


def _evaluate(case: ContextQualityCase, bundle: dict[str, Any]) -> dict[str, Any]:
    contexts = [dict(item) for item in bundle.get("related_contexts") or [] if isinstance(item, dict)]
    expected_files = set(case.expected_files)
    expected_terms = tuple(term.lower() for term in case.expected_terms)
    hit_expected_files = {
        str(item.get("path") or "")
        for item in contexts
        if str(item.get("path") or "") in expected_files
    }
    noise_contexts = []
    term_hits: set[str] = set()
    relationship_hits = 0
    for item in contexts:
        path = str(item.get("path") or "")
        snippet = str(item.get("snippet") or "").lower()
        relationship = str(item.get("relationship") or "")
        if relationship and relationship != "keyword_match":
            relationship_hits += 1
        if path in expected_files:
            for term in expected_terms:
                if term in snippet:
                    term_hits.add(term)
        else:
            noise_contexts.append(item)
    context_count = len(contexts)
    expected_file_recall = len(hit_expected_files) / len(expected_files) if expected_files else 1.0
    expected_term_coverage = len(term_hits) / len(expected_terms) if expected_terms else 1.0
    precision = (context_count - len(noise_contexts)) / context_count if context_count else 0.0
    relationship_richness = min(1.0, relationship_hits / context_count) if context_count else 0.0
    review_readiness_score = (
        expected_file_recall * 0.35
        + expected_term_coverage * 0.30
        + precision * 0.25
        + relationship_richness * 0.10
    )
    return {
        "source": dict(bundle.get("source_summary") or {}).get("primary_source") or "",
        "context_count": context_count,
        "expected_file_recall": round(expected_file_recall, 4),
        "expected_term_coverage": round(expected_term_coverage, 4),
        "precision": round(precision, 4),
        "noise_count": len(noise_contexts),
        "relationship_richness": round(relationship_richness, 4),
        "review_readiness_score": round(review_readiness_score * 100, 2),
        "hit_expected_files": sorted(hit_expected_files),
        "missing_expected_files": sorted(expected_files.difference(hit_expected_files)),
        "hit_terms": sorted(term_hits),
        "missing_terms": sorted(set(expected_terms).difference(term_hits)),
        "sample_contexts": _sample_contexts(contexts),
    }


def _sample_contexts(contexts: list[dict[str, object]], limit: int = 5) -> list[dict[str, object]]:
    samples = []
    for item in contexts[:limit]:
        snippet = re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip()
        samples.append(
            {
                "path": str(item.get("path") or ""),
                "line_number": int(item.get("line_number") or item.get("line_start") or 0),
                "symbol": str(item.get("symbol") or item.get("source_query") or ""),
                "relationship": str(item.get("relationship") or ""),
                "snippet": snippet[:240],
            }
        )
    return samples


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    tree_scores = [item["tree_sitter"]["review_readiness_score"] for item in results]
    keyword_scores = [item["keyword"]["review_readiness_score"] for item in results]
    tree_noise = [item["tree_sitter"]["noise_count"] for item in results]
    keyword_noise = [item["keyword"]["noise_count"] for item in results]
    tree_recall = [item["tree_sitter"]["expected_file_recall"] for item in results]
    keyword_recall = [item["keyword"]["expected_file_recall"] for item in results]
    tree_terms = [item["tree_sitter"]["expected_term_coverage"] for item in results]
    keyword_terms = [item["keyword"]["expected_term_coverage"] for item in results]
    return {
        "case_count": len(results),
        "tree_sitter_avg_score": round(_avg(tree_scores), 2),
        "keyword_avg_score": round(_avg(keyword_scores), 2),
        "score_delta": round(_avg(tree_scores) - _avg(keyword_scores), 2),
        "tree_sitter_avg_expected_file_recall": round(_avg(tree_recall), 4),
        "keyword_avg_expected_file_recall": round(_avg(keyword_recall), 4),
        "tree_sitter_avg_expected_term_coverage": round(_avg(tree_terms), 4),
        "keyword_avg_expected_term_coverage": round(_avg(keyword_terms), 4),
        "tree_sitter_total_noise": int(sum(tree_noise)),
        "keyword_total_noise": int(sum(keyword_noise)),
        "noise_reduction": int(sum(keyword_noise) - sum(tree_noise)),
    }


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Tree-sitter 关联上下文质量对比",
        "",
        "本报告使用离线 Java 问题样例，对比 Tree-sitter 结构化关联上下文与旧关键词检索。指标是检视输入质量的代理指标，不调用 LLM。",
        "",
        "## 总览",
        "",
        f"- 样例数：{summary['case_count']}",
        f"- Tree-sitter 平均可检视性评分：{summary['tree_sitter_avg_score']}",
        f"- 关键词平均可检视性评分：{summary['keyword_avg_score']}",
        f"- 评分提升：{summary['score_delta']}",
        f"- Tree-sitter 期望文件平均命中率：{summary['tree_sitter_avg_expected_file_recall']}",
        f"- 关键词期望文件平均命中率：{summary['keyword_avg_expected_file_recall']}",
        f"- Tree-sitter 期望证据词覆盖率：{summary['tree_sitter_avg_expected_term_coverage']}",
        f"- 关键词期望证据词覆盖率：{summary['keyword_avg_expected_term_coverage']}",
        f"- 噪声片段减少：{summary['noise_reduction']}",
        "",
        "## 分项结果",
        "",
        "| 用例 | Tree-sitter评分 | 关键词评分 | Tree-sitter命中率 | 关键词命中率 | Tree-sitter噪声 | 关键词噪声 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["results"]:
        tree = item["tree_sitter"]
        keyword = item["keyword"]
        lines.append(
            "| {case} | {tree_score:.2f} | {keyword_score:.2f} | {tree_recall:.2f} | {keyword_recall:.2f} | {tree_noise} | {keyword_noise} |".format(
                case=item["title"],
                tree_score=tree["review_readiness_score"],
                keyword_score=keyword["review_readiness_score"],
                tree_recall=tree["expected_file_recall"],
                keyword_recall=keyword["expected_file_recall"],
                tree_noise=tree["noise_count"],
                keyword_noise=keyword["noise_count"],
            )
        )
    lines.extend(["", "## 结论", ""])
    if summary["score_delta"] > 0:
        lines.append("Tree-sitter 在这组样例中提升了上下文可检视性，主要来自结构化调用/定义关系和更少的纯关键词噪声。")
    else:
        lines.append("Tree-sitter 在这组样例中没有形成稳定提升，需要继续增强符号抽取、调用链扩展和图谱索引。")
    lines.append("真实检视质量还需要结合 LLM 输出、人工确认结果和误报/漏报标注继续闭环评估。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(output_json: Path, output_markdown: Path, limit: int) -> dict[str, Any]:
    workspace = Path(tempfile.mkdtemp(prefix="code-graph-context-quality-"))
    try:
        results: list[dict[str, Any]] = []
        for case in _cases():
            repo_path = _materialize(case, workspace)
            tree_bundle = _run_planner(case, repo_path, mode="tree_sitter", limit=limit)
            keyword_bundle = _run_planner(case, repo_path, mode="keyword", limit=limit)
            results.append(
                {
                    "case_id": case.case_id,
                    "title": case.title,
                    "changed_files": list(case.changed_files),
                    "expected_files": list(case.expected_files),
                    "tree_sitter": _evaluate(case, tree_bundle),
                    "keyword": _evaluate(case, keyword_bundle),
                }
            )
        report = {
            "summary": _summarize(results),
            "results": results,
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_markdown(report, output_markdown)
        return report
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Tree-sitter context retrieval with keyword search.")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT / "output" / "code_graph_context_quality_comparison.json",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=REPO_ROOT / "output" / "code_graph_context_quality_comparison.md",
    )
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    report = run(args.output_json, args.output_md, args.limit)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"JSON: {args.output_json}")
    print(f"Markdown: {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
