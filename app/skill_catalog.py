"""技能目录扫描与 SKILL.md 元数据解析。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

import yaml


class SkillCard(TypedDict):
    """终端展示所需的技能卡片结构。"""

    name: str
    description: str
    path: str
    source: str


_FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_skill_md(skill_md_path: Path, source: str, project_root: Path) -> SkillCard | None:
    """解析单个 `SKILL.md`，提取前置 YAML 中的核心字段。"""
    content = skill_md_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_PATTERN.match(content)
    if not match:
        return None

    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        return None

    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    if not name or not description:
        return None

    relative_path = "/" + skill_md_path.relative_to(project_root).as_posix()
    return {
        "name": name,
        "description": description,
        "path": relative_path,
        "source": source,
    }


def list_skills(project_root: Path, sources: tuple[str, ...]) -> list[SkillCard]:
    """从多个技能源加载技能，后出现的同名技能会覆盖前者。"""
    resolved: dict[str, SkillCard] = {}

    for source in sources:
        source_dir = project_root / source.lstrip("/")
        if not source_dir.exists() or not source_dir.is_dir():
            continue

        for candidate in sorted(source_dir.iterdir()):
            if not candidate.is_dir():
                continue
            skill_md_path = candidate / "SKILL.md"
            if not skill_md_path.exists():
                continue
            parsed = _parse_skill_md(skill_md_path, source, project_root)
            if not parsed:
                continue
            resolved[parsed["name"]] = parsed

    return list(resolved.values())
