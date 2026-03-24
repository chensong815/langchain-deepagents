"""每轮重载并按会话过滤 skills 的 middleware。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from deepagents.middleware.skills import SkillsMiddleware, SkillsStateUpdate, _alist_skills, _list_skills

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langgraph.runtime import Runtime


class ReloadingSkillsMiddleware(SkillsMiddleware):
    """覆盖 deepagents 默认缓存行为，确保 skill 编辑后可立即生效。"""

    def __init__(
        self,
        *,
        backend,
        sources: list[str],
        allowed_skill_names: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(backend=backend, sources=sources)
        self.allowed_skill_names = allowed_skill_names

    def _filter_skills(self, skills: list[dict]) -> list[dict]:
        if self.allowed_skill_names is None:
            return skills
        allowed = set(self.allowed_skill_names)
        return [skill for skill in skills if str(skill.get("name") or "").strip() in allowed]

    def before_agent(self, state, runtime: Runtime, config: RunnableConfig) -> SkillsStateUpdate | None:  # type: ignore[override]
        backend = self._get_backend(state, runtime, config)
        all_skills: dict[str, dict] = {}
        for source_path in self.sources:
            for skill in self._filter_skills(_list_skills(backend, source_path)):
                all_skills[str(skill["name"])] = skill
        return SkillsStateUpdate(skills_metadata=list(all_skills.values()))

    async def abefore_agent(self, state, runtime: Runtime, config: RunnableConfig) -> SkillsStateUpdate | None:  # type: ignore[override]
        backend = self._get_backend(state, runtime, config)
        all_skills: dict[str, dict] = {}
        for source_path in self.sources:
            for skill in self._filter_skills(await _alist_skills(backend, source_path)):
                all_skills[str(skill["name"])] = skill
        return SkillsStateUpdate(skills_metadata=list(all_skills.values()))
