from __future__ import annotations

from pathlib import Path
import re
import yaml

from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.storage_factory import StorageRepositoryFactory
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_rule_screening_service import KnowledgeRuleScreeningService


class KnowledgeService:
    """统一封装知识文档的增删查和检索入口。"""

    def __init__(self, root: Path) -> None:
        """初始化知识库仓储、写入服务和检索服务。"""

        repository_factory = StorageRepositoryFactory(Path(root))
        self._repository = repository_factory.create_knowledge_repository()
        self._node_repository = repository_factory.create_knowledge_node_repository()
        self._ingestion = KnowledgeIngestionService(root)
        self._retrieval = KnowledgeRetrievalService(root)
        self._rule_screening = KnowledgeRuleScreeningService(root)

    def list_documents(self) -> list[KnowledgeDocument]:
        """列出知识库中的全部文档。"""

        return self._attach_indexed_outline(self._repository.list())

    def list_documents_for_expert(self, expert_id: str) -> list[KnowledgeDocument]:
        """列出某个专家当前绑定的全部文档。"""

        normalized = str(expert_id).strip()
        documents = [item for item in self._repository.list() if item.expert_id == normalized]
        return self._attach_indexed_outline(documents)

    def create_document(self, payload: dict[str, object]) -> KnowledgeDocument:
        """根据请求载荷创建并落盘一篇知识文档。"""

        document = KnowledgeDocument.model_validate(payload)
        return self._ingestion.ingest(document)

    def bootstrap_builtin_documents(self) -> int:
        """把仓库内置知识文档导入当前 SQLite，供实际审核链路使用。"""

        docs_root = Path(__file__).resolve().parents[1] / "storage" / "knowledge" / "docs"
        experts_root = Path(__file__).resolve().parents[1] / "builtin_experts"
        if not docs_root.exists():
            return 0
        enabled_expert_ids = self._load_enabled_builtin_expert_ids(experts_root)
        imported = 0
        for expert_dir in sorted(path for path in docs_root.iterdir() if path.is_dir()):
            expert_id = str(expert_dir.name).strip()
            if not expert_id:
                continue
            if enabled_expert_ids and expert_id not in enabled_expert_ids:
                continue
            for doc_path in sorted(expert_dir.glob("*.md")):
                content = doc_path.read_text(encoding="utf-8")
                self._ingestion.ingest(
                    KnowledgeDocument(
                        doc_id=doc_path.stem,
                        title=self._resolve_builtin_title(doc_path, content),
                        expert_id=expert_id,
                        doc_type="review_rule" if "RULE:" in content else "reference",
                        content=content,
                        tags=["builtin", expert_id],
                        source_filename=doc_path.name,
                        storage_path=str(doc_path),
                    )
                )
                imported += 1
        return imported

    def delete_document(self, doc_id: str) -> bool:
        """删除一篇知识文档。"""

        return self._repository.delete(doc_id)

    def retrieve_for_expert(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        """按专家和审核上下文检索最相关的知识文档。"""

        return self._retrieval.retrieve(expert_id, review_context)

    def clear_runtime_caches(self) -> None:
        """清理长生命周期检索缓存，避免后台常驻进程占用持续抬高。"""

        self._retrieval.clear_cache()

    def screen_rules_for_expert(
        self,
        expert_id: str,
        review_context: dict[str, object],
        runtime_settings: RuntimeSettings | None = None,
        analysis_mode: str = "standard",
        review_id: str = "",
    ) -> dict[str, object]:
        """遍历专家绑定的全部规则，对当前 MR 做程序化预筛查。"""

        return self._rule_screening.screen(
            expert_id,
            review_context,
            runtime_settings=runtime_settings,
            analysis_mode=analysis_mode,
            review_id=review_id,
        )

    def _attach_indexed_outline(self, documents: list[KnowledgeDocument]) -> list[KnowledgeDocument]:
        """为知识文档补齐章节索引，便于知识库与审核过程统一展示。"""

        if not documents:
            return documents
        nodes = self._node_repository.list_for_document_ids(
            [item.doc_id for item in documents if str(item.doc_id).strip()]
        )
        outline_by_doc: dict[str, list[str]] = {}
        for node in nodes:
            path = str(node.path or node.title or "").strip()
            if not path:
                continue
            outlines = outline_by_doc.setdefault(node.doc_id, [])
            if path not in outlines:
                outlines.append(path)
        for document in documents:
            document.indexed_outline = list(outline_by_doc.get(document.doc_id, []))
        return documents

    def _resolve_builtin_title(self, path: Path, content: str) -> str:
        """优先取 Markdown 首个标题，回退到文件名。"""

        for line in content.splitlines():
            match = re.match(r"^\s{0,3}#\s+(.+?)\s*$", line)
            if match:
                return str(match.group(1)).strip()
        return path.stem

    def _load_enabled_builtin_expert_ids(self, root: Path) -> set[str]:
        if not root.exists():
            return set()
        enabled_ids: set[str] = set()
        for expert_yaml in sorted(root.glob("*/expert.yaml")):
            payload = yaml.safe_load(expert_yaml.read_text(encoding="utf-8")) or {}
            expert_id = str(payload.get("expert_id") or "").strip()
            if not expert_id:
                continue
            if bool(payload.get("enabled", True)):
                enabled_ids.add(expert_id)
        return enabled_ids
