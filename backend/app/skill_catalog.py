"""技能目录扫描、SKILL.md 元数据解析与 schema 规范化。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

import yaml


class SkillCard(TypedDict, total=False):
    """终端展示所需的技能卡片结构。"""

    name: str
    description: str
    path: str
    source: str
    allowed_tools: list[str]
    triggers: list[str]
    required_slots: list[str]
    output_contract: str
    validation_errors: list[str]


_FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _parse_list_field(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        normalized = raw_value.replace(",", " ")
        return [item.strip() for item in normalized.split() if item.strip()]
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return []


def split_skill_document(content: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content.strip()

    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        frontmatter = {}

    if not isinstance(frontmatter, dict):
        frontmatter = {}
    body = content[match.end() :].strip()
    return frontmatter, body


def validate_skill_frontmatter(
    frontmatter: dict[str, Any],
    *,
    expected_path: str | None = None,
    expected_slug: str | None = None,
) -> list[str]:
    errors: list[str] = []

    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    path = str(frontmatter.get("path", "")).strip()

    if not name:
        errors.append("缺少 frontmatter.name")
    elif not _SKILL_NAME_PATTERN.fullmatch(name):
        errors.append("frontmatter.name 只允许小写字母、数字和中划线，且需以字母或数字开头")

    if not description:
        errors.append("缺少 frontmatter.description")

    if expected_path and path and path != expected_path:
        errors.append(f"frontmatter.path 必须为 {expected_path}")

    if expected_slug and name and name != expected_slug:
        errors.append(f"frontmatter.name 必须与技能目录名一致: {expected_slug}")

    return errors


def normalize_skill_frontmatter(
    frontmatter: dict[str, Any],
    *,
    expected_path: str | None = None,
    expected_slug: str | None = None,
) -> dict[str, Any]:
    normalized = dict(frontmatter)

    if "allowed_tools" in normalized and "allowed-tools" not in normalized:
        normalized["allowed-tools"] = normalized.pop("allowed_tools")
    if "required_slots" in normalized and "required-slots" not in normalized:
        normalized["required-slots"] = normalized.pop("required_slots")
    if "output_contract" in normalized and "output-contract" not in normalized:
        normalized["output-contract"] = normalized.pop("output_contract")

    normalized["name"] = str(normalized.get("name", "")).strip()
    normalized["description"] = str(normalized.get("description", "")).strip()
    if expected_path:
        normalized["path"] = expected_path
    elif "path" in normalized:
        normalized["path"] = str(normalized.get("path", "")).strip()

    normalized["allowed-tools"] = _parse_list_field(normalized.get("allowed-tools"))
    normalized["triggers"] = _parse_list_field(normalized.get("triggers"))
    normalized["required-slots"] = _parse_list_field(normalized.get("required-slots"))
    normalized["output-contract"] = str(normalized.get("output-contract", "")).strip()

    errors = validate_skill_frontmatter(
        normalized,
        expected_path=expected_path,
        expected_slug=expected_slug,
    )
    if errors:
        normalized["validation_errors"] = errors

    return normalized


def parse_skill_document(
    content: str,
    *,
    skill_md_path: Path,
    source: str,
    project_root: Path,
) -> SkillCard | None:
    """解析单个 `SKILL.md`，提取前置 YAML 中的核心字段。"""
    frontmatter, _ = split_skill_document(content)
    if not frontmatter:
        return None

    relative_path = "/" + skill_md_path.relative_to(project_root).as_posix()
    normalized = normalize_skill_frontmatter(
        frontmatter,
        expected_path=relative_path,
        expected_slug=skill_md_path.parent.name,
    )

    name = str(normalized.get("name", "")).strip()
    description = str(normalized.get("description", "")).strip()
    if not name or not description:
        return None

    return {
        "name": name,
        "description": description,
        "path": relative_path,
        "source": source,
        "allowed_tools": list(normalized.get("allowed-tools", [])),
        "triggers": list(normalized.get("triggers", [])),
        "required_slots": list(normalized.get("required-slots", [])),
        "output_contract": str(normalized.get("output-contract", "")).strip(),
        "validation_errors": list(normalized.get("validation_errors", [])),
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
            content = skill_md_path.read_text(encoding="utf-8")
            parsed = parse_skill_document(
                content,
                skill_md_path=skill_md_path,
                source=source,
                project_root=project_root,
            )
            if not parsed:
                continue
            resolved[parsed["name"]] = parsed

    return list(resolved.values())
