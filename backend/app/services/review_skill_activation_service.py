from __future__ import annotations

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.review_skill import ReviewSkillProfile


class ReviewSkillActivationService:
    """根据 review 上下文判断当前专家本轮应该激活哪些 skills。"""

    def activate(
        self,
        expert: ExpertProfile,
        subject: ReviewSubject,
        analysis_mode: str,
        available_skills: list[ReviewSkillProfile],
    ) -> list[ReviewSkillProfile]:
        design_docs = self._review_design_docs(subject)
        changed_blob = " ".join(subject.changed_files).lower()
        active: list[ReviewSkillProfile] = []
        for skill in available_skills:
            if not self._is_skill_bound_to_expert(expert, skill):
                continue
            if skill.applicable_experts and expert.expert_id not in skill.applicable_experts:
                continue
            if skill.allowed_modes and analysis_mode not in skill.allowed_modes:
                continue
            if skill.required_doc_types and not self._has_required_doc_types(design_docs, skill.required_doc_types):
                continue
            if skill.activation_hints and not any(hint.lower() in changed_blob for hint in skill.activation_hints):
                continue
            if "diff" in skill.required_context and not subject.unified_diff.strip():
                continue
            if "design_docs" in skill.required_context and not design_docs:
                continue
            active.append(skill)
        return active

    def _is_skill_bound_to_expert(self, expert: ExpertProfile, skill: ReviewSkillProfile) -> bool:
        """判断某个 skill 是否被绑定到当前专家。

        新机制优先读取扩展目录 metadata.json 里的 `bound_experts`，
        同时保留旧版 expert.skill_bindings 作为兼容入口。
        """
        if expert.expert_id in list(skill.bound_experts or []):
            return True
        return skill.skill_id in expert.skill_bindings

    def _review_design_docs(self, subject: ReviewSubject) -> list[dict[str, object]]:
        value = subject.metadata.get("design_docs", [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _has_required_doc_types(self, design_docs: list[dict[str, object]], required_doc_types: list[str]) -> bool:
        available = {str(item.get("doc_type") or "").strip() for item in design_docs}
        return all(doc_type in available for doc_type in required_doc_types)
