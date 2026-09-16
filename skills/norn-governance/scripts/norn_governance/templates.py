from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .managed_markdown import ManagedBlockError, parse_managed_blocks
from .models import (
    MANIFEST_SCHEMA_VERSION,
    ManagedFileRecord,
    NornManifest,
    OwnershipKind,
)


TEMPLATE_VERSION = 5
MANAGED_PATHS: Mapping[str, tuple[OwnershipKind, tuple[str, ...]]] = {
    "AGENTS.md": (OwnershipKind.MIXED, ("core-governance",)),
    "norn-governance/AGENTS.md": (
        OwnershipKind.MIXED,
        ("governance-directory",),
    ),
    "norn-governance/spec/AGENTS.md": (
        OwnershipKind.MIXED,
        ("specification-governance",),
    ),
    "norn-governance/spec/main-spec.md": (OwnershipKind.PROJECT, ()),
    "norn-governance/appendix/README.md": (
        OwnershipKind.MIXED,
        ("appendix-governance",),
    ),
}
MANIFEST_PATH = "norn-governance/.norn.json"
INITIALIZED_PATHS = (*MANAGED_PATHS, MANIFEST_PATH)


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def asset_template_root() -> Path:
    return skill_root() / "assets" / "ai-project-governance-template"


def legacy_template_root(version: int = 0) -> Path:
    return skill_root() / "assets" / "legacy-templates" / str(version)


def template_manifest(template_root: Path) -> NornManifest:
    records: dict[str, ManagedFileRecord] = {}
    for relative_path, (ownership, expected_blocks) in MANAGED_PATHS.items():
        path = Path(template_root) / relative_path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"模板文件无效 {relative_path}：{exc}") from exc
        try:
            blocks = parse_managed_blocks(text)
        except ManagedBlockError as exc:
            raise ValueError(f"模板受管区块无效 {relative_path}：{exc}") from exc
        if tuple(blocks) != expected_blocks:
            raise ValueError(
                f"模板受管区块与 {relative_path} 不匹配："
                f"期望 {expected_blocks}，实际 {tuple(blocks)}"
            )
        base_sha256 = blocks[expected_blocks[0]].sha256 if expected_blocks else None
        records[relative_path] = ManagedFileRecord(
            ownership=ownership,
            base_sha256=base_sha256,
            managed_blocks=expected_blocks,
            template_version=TEMPLATE_VERSION,
        )
    return NornManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        template_version=TEMPLATE_VERSION,
        managed_files=records,
    )


def manifest_bytes(manifest: NornManifest) -> bytes:
    return (
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")


def load_manifest(path: Path) -> NornManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Norn manifest 无效：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Norn manifest 无效：根节点必须是对象")
    return NornManifest.from_dict(payload)
