from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

from .managed_markdown import ManagedBlockError, parse_managed_blocks
from .models import (
    ActionKind,
    ConflictChoice,
    ConflictResolution,
    GovernanceTransaction,
    ManagedFileRecord,
    NornManifest,
    OwnershipKind,
    PathFingerprint,
    PathKind,
    TransactionAction,
    ProjectState,
    canonical_json_bytes,
    sha256_bytes,
    write_transaction,
)
from .templates import (
    INITIALIZED_PATHS,
    MANAGED_PATHS,
    MANIFEST_PATH,
    TEMPLATE_VERSION,
    asset_template_root,
    legacy_template_root,
    load_manifest,
    manifest_bytes,
)
from .managed_markdown import replace_managed_block


LEGACY_PATH_MAP = {
    "docs/AGENTS.md": "norn-governance/AGENTS.md",
    "docs/spec/AGENTS.md": "norn-governance/spec/AGENTS.md",
    "docs/spec/main-spec.md": "norn-governance/spec/main-spec.md",
    "docs/appendix/README.md": "norn-governance/appendix/README.md",
}
LEGACY_CONTENT_ROOTS = {
    "appendix": ("docs/appendix", "norn-governance/appendix"),
    "spec": ("docs/spec", "norn-governance/spec"),
}
GOVERNANCE_REFERENCE_MAP = {
    "docs/AGENTS.md": "norn-governance/AGENTS.md",
    "docs/spec/AGENTS.md": "norn-governance/spec/AGENTS.md",
    "docs/spec/main-spec.md": "norn-governance/spec/main-spec.md",
    "docs/appendix/README.md": "norn-governance/appendix/README.md",
}
ROOT_AGENTS = "AGENTS.md"
GOVERNANCE_DIRECTORY_PATHS = {
    "norn-governance",
    "norn-governance/spec",
    "norn-governance/appendix",
    "docs",
    "docs/spec",
    "docs/appendix",
}


def normalize_legacy_content_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for scope in scopes:
        if scope == "all":
            normalized.update(LEGACY_CONTENT_ROOTS)
        elif scope in LEGACY_CONTENT_ROOTS:
            normalized.add(scope)
        else:
            raise ValueError(f"不支持的旧内容范围：{scope}")
    return tuple(scope for scope in LEGACY_CONTENT_ROOTS if scope in normalized)


def fingerprint_path(path: Path) -> PathFingerprint:
    path = Path(path)
    if path.is_symlink():
        return PathFingerprint(True, PathKind.SYMLINK, None)
    if not path.exists():
        return PathFingerprint.missing()
    if path.is_file():
        return PathFingerprint(True, PathKind.FILE, sha256_bytes(path.read_bytes()))
    if path.is_dir():
        entries = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if child.is_symlink():
                kind = PathKind.SYMLINK
            elif child.is_file():
                kind = PathKind.FILE
            elif child.is_dir():
                kind = PathKind.DIRECTORY
            else:
                kind = PathKind.OTHER
            entries.append({"name": child.name, "kind": kind.value})
        digest = sha256_bytes(canonical_json_bytes({"entries": entries}))
        return PathFingerprint(True, PathKind.DIRECTORY, digest)
    return PathFingerprint(True, PathKind.OTHER, None)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _same_bytes(first: Path, second: Path) -> bool:
    return first.is_file() and second.is_file() and first.read_bytes() == second.read_bytes()


def _is_exact_legacy(target_root: Path, relative_path: str) -> bool:
    return _same_bytes(
        target_root / relative_path,
        legacy_template_root() / relative_path,
    )


def _has_structural_legacy_evidence(target_root: Path) -> bool:
    root = _read_text(target_root / ROOT_AGENTS)
    docs = _read_text(target_root / "docs/AGENTS.md")
    spec = _read_text(target_root / "docs/spec/AGENTS.md")
    if root is None or docs is None or spec is None:
        return False
    root_links = "docs/AGENTS.md" in root and "docs/spec/main-spec.md" in root
    docs_roles = "spec/" in docs and "appendix/" in docs
    spec_authority = "main-spec.md" in spec and any(
        term in spec for term in ("权威", "主要依据", "实现依据")
    )
    return root_links and docs_roles and spec_authority


def _manifest_state(target_root: Path) -> ProjectState:
    manifest_path = target_root / MANIFEST_PATH
    try:
        manifest = load_manifest(manifest_path)
    except ValueError:
        return ProjectState.CONFLICT
    if manifest.template_version > TEMPLATE_VERSION:
        return ProjectState.CONFLICT
    if set(manifest.managed_files) != set(MANAGED_PATHS):
        return ProjectState.CONFLICT
    for relative_path, (ownership, expected_blocks) in MANAGED_PATHS.items():
        path = target_root / relative_path
        fingerprint = fingerprint_path(path)
        if fingerprint.kind is not PathKind.FILE:
            return ProjectState.MIXED
        record = manifest.managed_files[relative_path]
        if (
            record.ownership is not ownership
            or record.managed_blocks != expected_blocks
            or record.template_version != manifest.template_version
        ):
            return ProjectState.CONFLICT
        if ownership is OwnershipKind.PROJECT:
            continue
        text = _read_text(path)
        if text is None:
            return ProjectState.CONFLICT
        try:
            blocks = parse_managed_blocks(text)
        except ManagedBlockError:
            return ProjectState.CONFLICT
        if tuple(blocks) != expected_blocks:
            return ProjectState.CONFLICT
        if blocks[expected_blocks[0]].sha256 != record.base_sha256:
            if manifest.template_version >= TEMPLATE_VERSION:
                return ProjectState.CONFLICT
    if manifest.template_version < TEMPLATE_VERSION:
        return ProjectState.UPGRADEABLE
    return ProjectState.CURRENT


def classify_project(target_root: Path) -> ProjectState:
    target_root = Path(target_root).resolve()
    if not target_root.is_dir():
        return ProjectState.CONFLICT

    for relative_path in GOVERNANCE_DIRECTORY_PATHS:
        fingerprint = fingerprint_path(target_root / relative_path)
        if fingerprint.exists and fingerprint.kind is not PathKind.DIRECTORY:
            return ProjectState.CONFLICT

    inspected_file_paths = {
        ROOT_AGENTS,
        MANIFEST_PATH,
        *MANAGED_PATHS,
        *LEGACY_PATH_MAP,
    }
    for relative_path in inspected_file_paths:
        fingerprint = fingerprint_path(target_root / relative_path)
        if fingerprint.exists and fingerprint.kind is not PathKind.FILE:
            return ProjectState.CONFLICT

    legacy_existing = {
        path for path in LEGACY_PATH_MAP if (target_root / path).exists()
    }
    norn_existing = any(
        (target_root / path).exists()
        for path in (*MANAGED_PATHS.keys(), MANIFEST_PATH)
        if path != ROOT_AGENTS
    )
    manifest_exists = (target_root / MANIFEST_PATH).exists()

    if manifest_exists:
        manifest_state = _manifest_state(target_root)
        if manifest_state is ProjectState.CONFLICT:
            return manifest_state
        if legacy_existing:
            return ProjectState.MIXED
        return manifest_state

    if norn_existing:
        return ProjectState.MIXED

    if legacy_existing:
        exact = {path for path in legacy_existing if _is_exact_legacy(target_root, path)}
        complete = legacy_existing == set(LEGACY_PATH_MAP)
        root_path = target_root / ROOT_AGENTS
        root_exact = _is_exact_legacy(target_root, ROOT_AGENTS)
        root_current = _same_bytes(root_path, asset_template_root() / ROOT_AGENTS)
        if complete and exact == set(LEGACY_PATH_MAP):
            if not root_path.exists() or root_exact:
                return ProjectState.LEGACY
            if root_current:
                return ProjectState.MIXED
            return ProjectState.CONFLICT
        if complete and _has_structural_legacy_evidence(target_root):
            governance_paths = {
                "docs/AGENTS.md",
                "docs/spec/AGENTS.md",
                "docs/appendix/README.md",
            }
            if not root_current:
                governance_paths.add(ROOT_AGENTS)
            if all(
                not (target_root / path).exists() or _is_exact_legacy(target_root, path)
                for path in governance_paths
            ):
                return ProjectState.MIXED if root_current else ProjectState.LEGACY
            return ProjectState.CONFLICT
        if exact:
            return ProjectState.MIXED
        return ProjectState.AMBIGUOUS

    root = target_root / ROOT_AGENTS
    if root.exists():
        if _is_exact_legacy(target_root, ROOT_AGENTS):
            return ProjectState.MIXED
        return ProjectState.CONFLICT
    return ProjectState.UNINITIALIZED


def _ownership_for(relative_path: str) -> OwnershipKind:
    if relative_path == MANIFEST_PATH:
        return OwnershipKind.MANAGED
    if relative_path in MANAGED_PATHS:
        return MANAGED_PATHS[relative_path][0]
    if relative_path.startswith(
        ("norn-governance/spec/", "norn-governance/appendix/")
    ):
        return OwnershipKind.PROJECT
    return OwnershipKind.MANAGED


def _write_artifact(artifact_root: Path, action_id: str, body: bytes) -> str:
    rendered_root = artifact_root / "rendered"
    rendered_root.mkdir(parents=True, exist_ok=True)
    target = rendered_root / f"{action_id}.content"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{action_id}.", suffix=".tmp", dir=rendered_root
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return sha256_bytes(body)


def _action_id(prefix: str, relative_path: str) -> str:
    normalized = relative_path.replace("/", "-").replace(".", "-")
    return f"{prefix}-{normalized}".strip("-")


def _keep_action(
    target_root: Path,
    relative_path: str,
    *,
    reason: str,
) -> TransactionAction:
    return TransactionAction(
        action_id=_action_id("keep", relative_path),
        kind=ActionKind.KEEP,
        source_path=None,
        target_path=relative_path,
        source_before=None,
        target_before=fingerprint_path(target_root / relative_path),
        output_sha256=None,
        ownership=_ownership_for(relative_path),
        evidence=("canonical 目标已具有治理事务的输出字节",),
        reason=reason,
        risk="不修改文件",
        verification=("目标指纹保持不变",),
        allowed_resolutions=(),
    )


def _conflict_action(
    target_root: Path,
    target_path: str,
    *,
    source_path: str | None = None,
    reason: str,
    evidence: Iterable[str],
    allowed_resolutions: tuple[ConflictChoice, ...] = (),
) -> TransactionAction:
    return TransactionAction(
        action_id=_action_id("conflict", target_path),
        kind=ActionKind.CONFLICT,
        source_path=source_path,
        target_path=target_path,
        source_before=(
            fingerprint_path(target_root / source_path) if source_path else None
        ),
        target_before=fingerprint_path(target_root / target_path),
        output_sha256=None,
        ownership=_ownership_for(target_path),
        evidence=tuple(evidence),
        reason=reason,
        risk="解决冲突前禁止自动执行",
        verification=("重新分析后不包含未解决的冲突动作",),
        allowed_resolutions=allowed_resolutions,
    )


def _delete_action(
    target_root: Path,
    relative_path: str,
    *,
    reason: str,
) -> TransactionAction:
    return TransactionAction(
        action_id=_action_id("delete", relative_path),
        kind=ActionKind.DELETE,
        source_path=None,
        target_path=relative_path,
        source_before=None,
        target_before=fingerprint_path(target_root / relative_path),
        output_sha256=None,
        ownership=_ownership_for(relative_path),
        evidence=("治理事务已验证 canonical 替代内容",),
        reason=reason,
        risk="仅删除已取得指纹的旧重复项或空目录",
        verification=("执行后路径不存在",),
        allowed_resolutions=(),
    )


def _transaction_output(
    target_root: Path,
    artifact_root: Path,
    *,
    target_path: str,
    body: bytes,
    reason: str,
    evidence: Iterable[str],
    source_path: str | None = None,
    allow_existing_replace: bool = False,
    byte_identical_move: bool = False,
) -> list[TransactionAction]:
    target = target_root / target_path
    source = target_root / source_path if source_path else None
    current_parent = target_root
    for part in Path(target_path).parts[:-1]:
        current_parent /= part
        parent_fingerprint = fingerprint_path(current_parent)
        if parent_fingerprint.kind not in {PathKind.MISSING, PathKind.DIRECTORY}:
            return [
                _conflict_action(
                    target_root,
                    target_path,
                    source_path=source_path,
                    reason="目标父路径不是普通目录",
                    evidence=(
                        *evidence,
                        "父路径 "
                        f"{current_parent.relative_to(target_root).as_posix()} "
                        f"的类型为 {parent_fingerprint.kind.value}",
                    ),
                )
            ]
    target_before = fingerprint_path(target)
    if target_before.kind not in {PathKind.MISSING, PathKind.FILE}:
        return [
            _conflict_action(
                target_root,
                target_path,
                source_path=source_path,
                reason="目标路径不是普通文件",
                evidence=(*evidence, f"目标类型为 {target_before.kind.value}"),
            )
        ]

    if target_before.kind is PathKind.FILE and target.read_bytes() == body:
        actions = [
            _keep_action(
                target_root,
                target_path,
                reason="canonical 目标已与治理事务输出一致",
            )
        ]
        if source is not None and source_path != target_path:
            actions.append(
                _delete_action(
                    target_root,
                    source_path,
                    reason=f"保留 {target_path} 后删除旧重复项",
                )
            )
        return actions

    if target_before.kind is PathKind.FILE and not allow_existing_replace:
        choices = (ConflictChoice.SEMANTIC_MERGE,)
        if _ownership_for(target_path) is not OwnershipKind.PROJECT:
            choices = (
                ConflictChoice.ADOPT_TEMPLATE,
                ConflictChoice.SEMANTIC_MERGE,
            )
        return [
            _conflict_action(
                target_root,
                target_path,
                source_path=source_path,
                reason="canonical 目标与治理事务迁移输出不同",
                evidence=(*evidence, "目标 SHA-256 不同"),
                allowed_resolutions=choices,
            )
        ]

    is_merge = source_path is not None or allow_existing_replace
    kind = (
        ActionKind.MOVE
        if byte_identical_move
        else ActionKind.MERGE
        if is_merge
        else ActionKind.CREATE
    )
    action_id = _action_id(kind.value, target_path)
    output_sha256 = _write_artifact(artifact_root, action_id, body)
    return [
        TransactionAction(
            action_id=action_id,
            kind=kind,
            source_path=source_path,
            target_path=target_path,
            source_before=(fingerprint_path(source) if source is not None else None),
            target_before=target_before,
            output_sha256=output_sha256,
            ownership=_ownership_for(target_path),
            evidence=tuple(evidence),
            reason=reason,
            risk=(
                "删除旧源文件前先写入并验证目标"
                if source_path
                else "创建新的受管文件"
            ),
            verification=("目标 SHA-256 与治理事务输出一致",)
            + (("旧源文件不存在",) if source_path else ()),
            allowed_resolutions=(),
        )
    ]


def _render_project_spec(source: Path) -> bytes:
    text = source.read_text(encoding="utf-8")
    for legacy_reference, norn_reference in GOVERNANCE_REFERENCE_MAP.items():
        text = text.replace(legacy_reference, norn_reference)
    return text.encode("utf-8")


def _projected_directory_is_empty(
    target_root: Path,
    relative_path: str,
    removable_paths: set[str],
) -> bool:
    directory = target_root / relative_path
    if not directory.is_dir() or directory.is_symlink():
        return False
    for child in directory.iterdir():
        child_relative = child.relative_to(target_root).as_posix()
        if child_relative not in removable_paths:
            return False
    return True


def _append_directory_cleanup(
    target_root: Path,
    actions: list[TransactionAction],
    explicit_source_directories: Iterable[str] = (),
) -> None:
    explicit_source_directories = tuple(explicit_source_directories)
    explicit_roots = {
        directory
        for directory in explicit_source_directories
        if directory in {"docs/spec", "docs/appendix"}
    }
    removable_paths = {
        action.source_path
        for action in actions
        if action.source_path and action.kind in {ActionKind.MERGE, ActionKind.MOVE}
    }
    removable_paths.update(
        action.target_path
        for action in actions
        if action.kind is ActionKind.DELETE
        and (
            action.target_path in LEGACY_PATH_MAP
            or any(
                action.target_path.startswith(f"{root}/")
                for root in explicit_roots
            )
        )
    )
    for directory in sorted(
        set(explicit_source_directories),
        key=lambda path: len(Path(path).parts),
        reverse=True,
    ):
        if directory in {"docs/spec", "docs/appendix"}:
            continue
        if _projected_directory_is_empty(target_root, directory, removable_paths):
            actions.append(
                _delete_action(
                    target_root,
                    directory,
                    reason="删除显式迁移后留下的空旧子目录",
                )
            )
            removable_paths.add(directory)
    for directory in ("docs/spec", "docs/appendix"):
        if _projected_directory_is_empty(target_root, directory, removable_paths):
            actions.append(
                _delete_action(
                    target_root,
                    directory,
                    reason="全部受管子项迁移后删除旧目录",
                )
            )
            removable_paths.add(directory)
    if _projected_directory_is_empty(target_root, "docs", removable_paths):
        actions.append(
            _delete_action(
                target_root,
                "docs",
                reason="旧 docs 目录变空后将其删除",
            )
        )


def _analyze_explicit_legacy_content(
    target_root: Path,
    artifact_root: Path,
    scopes: tuple[str, ...],
) -> tuple[list[TransactionAction], set[str]]:
    actions: list[TransactionAction] = []
    source_directories: set[str] = set()

    def visit(
        source_directory: Path,
        source_root: Path,
        target_root_path: Path,
    ) -> None:
        source_relative = source_directory.relative_to(target_root).as_posix()
        source_directories.add(source_relative)
        for child in sorted(source_directory.iterdir(), key=lambda path: path.name):
            child_relative = child.relative_to(target_root).as_posix()
            relative_inside_tree = child.relative_to(source_root)
            target_relative = (target_root_path / relative_inside_tree).as_posix()
            if child_relative in LEGACY_PATH_MAP:
                continue
            fingerprint = fingerprint_path(child)
            if fingerprint.kind is PathKind.DIRECTORY:
                visit(child, source_root, target_root_path)
            elif fingerprint.kind is PathKind.FILE:
                actions.extend(
                    _transaction_output(
                        target_root,
                        artifact_root,
                        source_path=child_relative,
                        target_path=target_relative,
                        body=child.read_bytes(),
                        reason=(
                            "逐字节移动用户明确要求迁移的旧治理内容"
                        ),
                        evidence=(
                            "用户明确要求递归迁移 "
                            f"{source_root.relative_to(target_root).as_posix()}/",
                        ),
                        byte_identical_move=True,
                    )
                )
            else:
                actions.append(
                    _conflict_action(
                        target_root,
                        target_relative,
                        source_path=child_relative,
                        reason=(
                            "明确纳入范围的旧内容使用了不支持的文件系统类型"
                        ),
                        evidence=(f"源路径类型为 {fingerprint.kind.value}",),
                    )
                )

    for scope in scopes:
        source_relative, target_relative = LEGACY_CONTENT_ROOTS[scope]
        source_root = target_root / source_relative
        if not source_root.exists() and not source_root.is_symlink():
            continue
        fingerprint = fingerprint_path(source_root)
        if fingerprint.kind is not PathKind.DIRECTORY:
            actions.append(
                _conflict_action(
                    target_root,
                    target_relative,
                    source_path=source_relative,
                    reason="明确纳入范围的旧内容根路径不是普通目录",
                    evidence=(f"源路径类型为 {fingerprint.kind.value}",),
                )
            )
            continue
        visit(source_root, source_root, Path(target_relative))
    return actions, source_directories


def _analyze_uninitialized(
    target_root: Path,
    artifact_root: Path,
) -> list[TransactionAction]:
    actions: list[TransactionAction] = []
    source_root = asset_template_root()
    for relative_path in INITIALIZED_PATHS:
        actions.extend(
            _transaction_output(
                target_root,
                artifact_root,
                target_path=relative_path,
                body=(source_root / relative_path).read_bytes(),
                reason="初始化当前 Norn 治理结构",
                evidence=("受管目标路径不存在",),
            )
        )
    return actions


def _analyze_ambiguous(target_root: Path) -> list[TransactionAction]:
    actions: list[TransactionAction] = []
    for source_path, target_path in LEGACY_PATH_MAP.items():
        if not (target_root / source_path).exists():
            continue
        actions.append(
            _conflict_action(
                target_root,
                target_path,
                source_path=source_path,
                reason="旧内容归属证据不足",
                evidence=(
                    "路径名称与旧布局相符，但不存在已知模板哈希或完整证据链",
                ),
                allowed_resolutions=(ConflictChoice.SEMANTIC_MERGE,),
            )
        )
    return actions


def _root_actions(
    target_root: Path,
    artifact_root: Path,
    structural_evidence: bool,
) -> list[TransactionAction]:
    current_root = asset_template_root() / ROOT_AGENTS
    target = target_root / ROOT_AGENTS
    if not target.exists():
        return _transaction_output(
            target_root,
            artifact_root,
            target_path=ROOT_AGENTS,
            body=current_root.read_bytes(),
            reason="创建当前 Norn 根入口",
            evidence=("根 AGENTS.md 不存在",),
        )
    if _same_bytes(target, current_root):
        return [_keep_action(target_root, ROOT_AGENTS, reason="根入口为当前版本")]
    if _is_exact_legacy(target_root, ROOT_AGENTS):
        return _transaction_output(
            target_root,
            artifact_root,
            target_path=ROOT_AGENTS,
            body=current_root.read_bytes(),
            reason="原位升级与旧模板完全一致的根入口",
            evidence=("根文件 SHA-256 与旧版本 0 一致",),
            allow_existing_replace=True,
        )
    if structural_evidence:
        return [
            _conflict_action(
                target_root,
                ROOT_AGENTS,
                reason="自定义旧根入口需要语义选择",
                evidence=("根文件属于完整旧治理证据链",),
                allowed_resolutions=(
                    ConflictChoice.ADOPT_TEMPLATE,
                    ConflictChoice.SEMANTIC_MERGE,
                ),
            )
        ]
    return [
        _conflict_action(
            target_root,
            ROOT_AGENTS,
            reason="现有根 AGENTS.md 没有可识别的 Norn 基线",
            evidence=("根文件内容与当前和旧模板均不同",),
            allowed_resolutions=(ConflictChoice.SEMANTIC_MERGE,),
        )
    ]


def _analyze_legacy_or_mixed(
    target_root: Path,
    artifact_root: Path,
    legacy_content_scopes: tuple[str, ...] = (),
) -> list[TransactionAction]:
    actions: list[TransactionAction] = []
    structural_evidence = _has_structural_legacy_evidence(target_root)
    actions.extend(_root_actions(target_root, artifact_root, structural_evidence))

    current_root = asset_template_root()
    for source_path, target_path in LEGACY_PATH_MAP.items():
        source = target_root / source_path
        if not source.exists():
            target = target_root / target_path
            if target.is_file():
                if target_path.endswith("main-spec.md"):
                    actions.append(
                        _keep_action(
                            target_root,
                            target_path,
                            reason="项目自有规格已使用 Norn 路径",
                        )
                    )
                elif _same_bytes(target, current_root / target_path):
                    actions.append(
                        _keep_action(
                            target_root,
                            target_path,
                            reason="canonical 治理目标为当前版本",
                        )
                    )
                else:
                    actions.append(
                        _conflict_action(
                            target_root,
                            target_path,
                            reason="部分 Norn 目标没有对应的迁移源",
                            evidence=("目标存在，但 manifest 或旧源文件不存在",),
                            allowed_resolutions=(
                                ConflictChoice.ADOPT_TEMPLATE,
                                ConflictChoice.SEMANTIC_MERGE,
                            ),
                        )
                    )
                continue
            actions.extend(
                _transaction_output(
                    target_root,
                    artifact_root,
                    target_path=target_path,
                    body=(current_root / target_path).read_bytes(),
                    reason="补全缺失的 Norn 治理路径",
                    evidence=("目标与旧源文件均不存在",),
                )
            )
            continue

        exact_legacy = _is_exact_legacy(target_root, source_path)
        if target_path.endswith("main-spec.md") and (exact_legacy or structural_evidence):
            actions.extend(
                _transaction_output(
                    target_root,
                    artifact_root,
                    source_path=source_path,
                    target_path=target_path,
                    body=_render_project_spec(source),
                    reason="迁移项目自有规格并更新明确的治理引用",
                    evidence=(
                        "模板哈希或证据链已确认项目规格归属",
                    ),
                )
            )
        elif exact_legacy:
            actions.extend(
                _transaction_output(
                    target_root,
                    artifact_root,
                    source_path=source_path,
                    target_path=target_path,
                    body=(current_root / target_path).read_bytes(),
                    reason="升级并迁移与旧治理模板完全一致的文件",
                    evidence=(f"{source_path} 的 SHA-256 与旧版本 0 一致",),
                )
            )
        elif structural_evidence:
            actions.append(
                _conflict_action(
                    target_root,
                    target_path,
                    source_path=source_path,
                    reason="自定义旧治理内容需要语义选择",
                    evidence=("存在完整的旧治理结构证据链",),
                    allowed_resolutions=(
                        ConflictChoice.ADOPT_TEMPLATE,
                        ConflictChoice.SEMANTIC_MERGE,
                    ),
                )
            )
        else:
            actions.append(
                _conflict_action(
                    target_root,
                    target_path,
                    source_path=source_path,
                    reason="旧内容归属证据不足",
                    evidence=("孤立路径与任何已知旧模板均不匹配",),
                    allowed_resolutions=(ConflictChoice.SEMANTIC_MERGE,),
                )
            )

    explicit_actions, source_directories = _analyze_explicit_legacy_content(
        target_root,
        artifact_root,
        legacy_content_scopes,
    )
    actions.extend(explicit_actions)
    if not any(action.kind is ActionKind.CONFLICT for action in actions):
        actions.extend(
            _transaction_output(
                target_root,
                artifact_root,
                target_path=MANIFEST_PATH,
                body=(current_root / MANIFEST_PATH).read_bytes(),
                reason="治理事务完全解决后写入当前 manifest",
                evidence=("全部受管内容动作均为确定性动作",),
            )
        )
    _append_directory_cleanup(target_root, actions, source_directories)
    return actions


def _current_keep_actions(target_root: Path) -> list[TransactionAction]:
    return [
        _keep_action(target_root, path, reason="受管路径为当前版本")
        for path in INITIALIZED_PATHS
    ]


def _analyze_upgrade(
    target_root: Path,
    artifact_root: Path,
) -> list[TransactionAction]:
    manifest = load_manifest(target_root / MANIFEST_PATH)
    actions: list[TransactionAction] = []
    current_templates = asset_template_root()
    for relative_path, (ownership, block_ids) in MANAGED_PATHS.items():
        if ownership is OwnershipKind.PROJECT:
            actions.append(
                _keep_action(
                    target_root,
                    relative_path,
                    reason="升级不会替换项目自有规格",
                )
            )
            continue
        current_text = (target_root / relative_path).read_text(encoding="utf-8")
        current_blocks = parse_managed_blocks(current_text)
        block_id = block_ids[0]
        record = manifest.managed_files[relative_path]
        if current_blocks[block_id].sha256 != record.base_sha256:
            actions.append(
                _conflict_action(
                    target_root,
                    relative_path,
                    reason="受管区块与记录的基线不同",
                    evidence=(
                        "当前受管区块 SHA-256 与 manifest 基线不同",
                    ),
                    allowed_resolutions=(
                        ConflictChoice.KEEP_CURRENT,
                        ConflictChoice.ADOPT_TEMPLATE,
                        ConflictChoice.SEMANTIC_MERGE,
                    ),
                )
            )
            continue
        template_text = (current_templates / relative_path).read_text(encoding="utf-8")
        template_block = parse_managed_blocks(template_text)[block_id].text
        rendered = replace_managed_block(current_text, block_id, template_block)
        actions.extend(
            _transaction_output(
                target_root,
                artifact_root,
                target_path=relative_path,
                body=rendered.encode("utf-8"),
                reason="升级未修改的受管区块并保留项目文本",
                evidence=("当前区块 SHA-256 与记录的基线一致",),
                allow_existing_replace=True,
            )
        )
    if not any(action.kind is ActionKind.CONFLICT for action in actions):
        actions.extend(
            _transaction_output(
                target_root,
                artifact_root,
                target_path=MANIFEST_PATH,
                body=(current_templates / MANIFEST_PATH).read_bytes(),
                reason="全部受管区块升级后更新 manifest",
                evidence=("每个受管区块都有确定的最终基线",),
                allow_existing_replace=True,
            )
        )
    return actions


def _resolved_transaction_state(transaction: GovernanceTransaction) -> ProjectState:
    if transaction.project_state is ProjectState.UPGRADEABLE:
        return ProjectState.UPGRADEABLE
    if any(
        action.source_path and action.source_path.startswith("docs/")
        for action in transaction.actions
    ):
        return ProjectState.LEGACY
    return ProjectState.MIXED


def _final_governed_body(
    target_root: Path,
    artifact_root: Path,
    actions: list[TransactionAction],
    relative_path: str,
) -> bytes:
    candidates = [
        action for action in actions if action.target_path == relative_path
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"已解决事务必须为 {relative_path} 包含且仅包含一个动作"
        )
    action = candidates[0]
    if action.output_sha256 is not None:
        artifact = artifact_root / "rendered" / f"{action.action_id}.content"
        body = artifact.read_bytes()
        if sha256_bytes(body) != action.output_sha256:
            raise ValueError(f"渲染产物哈希与 {action.action_id} 不匹配")
        return body
    path = target_root / relative_path
    if action.kind is ActionKind.KEEP and path.is_file():
        return path.read_bytes()
    raise ValueError(f"已解决动作没有 {relative_path} 的最终内容")


def _resolved_manifest(
    target_root: Path,
    artifact_root: Path,
    actions: list[TransactionAction],
) -> NornManifest:
    records: dict[str, ManagedFileRecord] = {}
    for relative_path, (ownership, block_ids) in MANAGED_PATHS.items():
        body = _final_governed_body(
            target_root, artifact_root, actions, relative_path
        )
        if ownership is OwnershipKind.PROJECT:
            base_sha256 = None
        else:
            try:
                blocks = parse_managed_blocks(body.decode("utf-8"))
            except (UnicodeDecodeError, ManagedBlockError) as exc:
                raise ValueError(
                    f"{relative_path} 的已解决受管内容无效：{exc}"
                ) from exc
            if tuple(blocks) != block_ids:
                raise ValueError(
                    f"{relative_path} 的已解决受管区块不匹配"
                )
            base_sha256 = blocks[block_ids[0]].sha256
        records[relative_path] = ManagedFileRecord(
            ownership=ownership,
            base_sha256=base_sha256,
            managed_blocks=block_ids,
            template_version=TEMPLATE_VERSION,
        )
    return NornManifest(
        schema_version=1,
        template_version=TEMPLATE_VERSION,
        managed_files=records,
    )


def resolve_conflicts(
    transaction: GovernanceTransaction,
    choices: tuple[ConflictResolution, ...],
    artifact_root: Path,
) -> GovernanceTransaction:
    if transaction.transaction_sha256 != transaction.expected_digest():
        raise ValueError("治理事务摘要不匹配")
    artifact_root = Path(artifact_root).resolve()
    target_root = Path(transaction.target_root)
    conflicts = {
        action.action_id: action
        for action in transaction.actions
        if action.kind is ActionKind.CONFLICT
    }
    choice_map: dict[str, ConflictResolution] = {}
    for resolution in choices:
        if resolution.action_id in choice_map:
            raise ValueError(f"冲突选择重复：{resolution.action_id}")
        choice_map[resolution.action_id] = resolution
    if set(choice_map) != set(conflicts):
        missing = sorted(set(conflicts) - set(choice_map))
        extra = sorted(set(choice_map) - set(conflicts))
        raise ValueError(f"冲突选择不匹配：缺少={missing}，多余={extra}")

    resolved_actions: list[TransactionAction] = []
    for action in transaction.actions:
        if action.kind is not ActionKind.CONFLICT:
            if action.target_path != MANIFEST_PATH:
                resolved_actions.append(action)
            continue
        resolution = choice_map[action.action_id]
        if resolution.choice not in action.allowed_resolutions:
            raise ValueError(
                f"动作 {action.action_id} 不允许选择 {resolution.choice.value}"
            )
        if resolution.choice is ConflictChoice.KEEP_CURRENT:
            resolved_actions.append(
                TransactionAction(
                    action_id=action.action_id,
                    kind=ActionKind.KEEP,
                    source_path=None,
                    target_path=action.target_path,
                    source_before=None,
                    target_before=action.target_before,
                    output_sha256=None,
                    ownership=action.ownership,
                    evidence=(*action.evidence, "用户选择保留当前受管区块"),
                    reason="将当前受管区块保留为已接受基线",
                    risk="当前项目定制将成为新基线",
                    verification=("目标指纹保持不变",),
                    allowed_resolutions=(),
                )
            )
            continue

        if resolution.choice is ConflictChoice.ADOPT_TEMPLATE:
            if action.target_path not in MANAGED_PATHS:
                raise ValueError(
                    f"冲突 {action.action_id} 没有 canonical 模板"
                )
            body = (asset_template_root() / action.target_path).read_bytes()
        else:
            assert resolution.rendered_path is not None
            semantic_path = Path(resolution.rendered_path).resolve()
            if not semantic_path.is_relative_to(artifact_root):
                raise ValueError("语义融合产物必须位于产物根目录内")
            try:
                body = semantic_path.read_bytes()
                body.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ValueError(f"语义融合产物无效：{exc}") from exc
            if sha256_bytes(body) != resolution.rendered_sha256:
                raise ValueError("语义融合产物哈希不匹配")
            if action.target_path in MANAGED_PATHS:
                _, expected_blocks = MANAGED_PATHS[action.target_path]
                if expected_blocks:
                    blocks = parse_managed_blocks(body.decode("utf-8"))
                    if tuple(blocks) != expected_blocks:
                        raise ValueError("语义融合产物的受管区块不匹配")

        output_sha256 = _write_artifact(artifact_root, action.action_id, body)
        kind = (
            ActionKind.MERGE
            if action.source_path or action.target_before.exists
            else ActionKind.CREATE
        )
        resolved_actions.append(
            TransactionAction(
                action_id=action.action_id,
                kind=kind,
                source_path=action.source_path,
                target_path=action.target_path,
                source_before=action.source_before,
                target_before=action.target_before,
                output_sha256=output_sha256,
                ownership=action.ownership,
                evidence=(*action.evidence, f"用户选择 {resolution.choice.value}"),
                reason="应用已明确解决的治理内容",
                risk="仅写入用户选择且受哈希绑定的结果",
                verification=(
                    "目标 SHA-256 与已解决输出一致",
                )
                + (("旧源文件不存在",) if action.source_path else ()),
                allowed_resolutions=(),
            )
        )

    directory_paths = {"docs/spec", "docs/appendix", "docs"}
    resolved_actions = [
        action
        for action in resolved_actions
        if not (
            action.kind is ActionKind.DELETE
            and action.target_path in directory_paths
        )
    ]
    _append_directory_cleanup(target_root, resolved_actions)
    manifest = _resolved_manifest(target_root, artifact_root, resolved_actions)
    manifest_body = manifest_bytes(manifest)
    manifest_action_id = _action_id("merge", MANIFEST_PATH)
    manifest_sha256 = _write_artifact(
        artifact_root, manifest_action_id, manifest_body
    )
    manifest_before = fingerprint_path(target_root / MANIFEST_PATH)
    resolved_actions.append(
        TransactionAction(
            action_id=manifest_action_id,
            kind=(
                ActionKind.MERGE if manifest_before.exists else ActionKind.CREATE
            ),
            source_path=None,
            target_path=MANIFEST_PATH,
            source_before=None,
            target_before=manifest_before,
            output_sha256=manifest_sha256,
            ownership=OwnershipKind.MANAGED,
            evidence=("每个冲突都有经过验证的明确解决方案",),
            reason="解决冲突后记录最终受管基线",
            risk="只有执行成功后才把迁移或升级标记为完成",
            verification=("manifest 字节和受管基线与最终内容一致",),
            allowed_resolutions=(),
        )
    )
    resolved = GovernanceTransaction.build(
        target_root=transaction.target_root,
        project_state=_resolved_transaction_state(transaction),
        template_version=TEMPLATE_VERSION,
        actions=resolved_actions,
        conflicts=(),
    )
    write_transaction(resolved, artifact_root)
    return resolved


def _blocking_state_actions(
    target_root: Path,
    artifact_root: Path,
    state: ProjectState,
) -> list[TransactionAction]:
    if state is ProjectState.AMBIGUOUS:
        return _analyze_ambiguous(target_root)
    for relative_path in (ROOT_AGENTS, MANIFEST_PATH, *LEGACY_PATH_MAP):
        fingerprint = fingerprint_path(target_root / relative_path)
        if fingerprint.kind in {
            PathKind.DIRECTORY,
            PathKind.SYMLINK,
            PathKind.OTHER,
        }:
            return [
                _conflict_action(
                    target_root,
                    relative_path,
                    reason="受管路径使用了不支持的文件系统类型",
                    evidence=(f"观察到的路径类型为 {fingerprint.kind.value}",),
                )
            ]
    if (target_root / MANIFEST_PATH).exists():
        return [
            _conflict_action(
                target_root,
                MANIFEST_PATH,
                reason="manifest 无效或版本高于当前 Skill",
                evidence=("manifest 校验或版本兼容性检查失败",),
            )
        ]
    actions = [
        action
        for action in _analyze_uninitialized(target_root, artifact_root)
        if action.target_path != ROOT_AGENTS
    ]
    actions.append(
        _conflict_action(
            target_root,
            ROOT_AGENTS,
            reason="现有根 AGENTS.md 没有可识别的 Norn 基线",
            evidence=("根文件内容与当前和旧模板均不同",),
            allowed_resolutions=(ConflictChoice.SEMANTIC_MERGE,),
        )
    )
    return actions


def analyze_governance(
    target_root: Path,
    artifact_root: Path,
    *,
    legacy_content_scopes: Iterable[str] = (),
) -> GovernanceTransaction:
    target_root = Path(target_root).resolve()
    artifact_root = Path(artifact_root).resolve()
    normalized_scopes = normalize_legacy_content_scopes(legacy_content_scopes)
    if not target_root.is_dir():
        raise ValueError(f"目标根路径不是目录：{target_root}")
    if artifact_root == target_root or artifact_root.is_relative_to(target_root):
        raise ValueError("产物根目录必须位于目标仓库之外")
    artifact_root.mkdir(parents=True, exist_ok=True)
    state = classify_project(target_root)

    handled_explicit_content = False
    if state is ProjectState.UNINITIALIZED:
        actions = _analyze_uninitialized(target_root, artifact_root)
    elif state is ProjectState.CURRENT:
        actions = _current_keep_actions(target_root)
    elif state is ProjectState.UPGRADEABLE:
        actions = _analyze_upgrade(target_root, artifact_root)
    elif state is ProjectState.AMBIGUOUS:
        actions = _analyze_ambiguous(target_root)
    elif any((target_root / path).exists() for path in LEGACY_PATH_MAP):
        actions = _analyze_legacy_or_mixed(
            target_root,
            artifact_root,
            normalized_scopes,
        )
        handled_explicit_content = True
    elif state is ProjectState.MIXED:
        actions = _analyze_legacy_or_mixed(
            target_root,
            artifact_root,
            normalized_scopes,
        )
        handled_explicit_content = True
    else:
        actions = _blocking_state_actions(target_root, artifact_root, state)

    explicit_actions: list[TransactionAction] = []
    if normalized_scopes and not handled_explicit_content:
        explicit_actions, source_directories = _analyze_explicit_legacy_content(
            target_root,
            artifact_root,
            normalized_scopes,
        )
        first_explicit_action = len(actions)
        actions.extend(explicit_actions)
        _append_directory_cleanup(target_root, actions, source_directories)
        explicit_actions = actions[first_explicit_action:]

    if any(action.kind is ActionKind.CONFLICT for action in actions):
        effective_state = (
            ProjectState.AMBIGUOUS
            if state is ProjectState.AMBIGUOUS
            else ProjectState.CONFLICT
        )
    else:
        effective_state = (
            ProjectState.MIXED
            if state is ProjectState.CURRENT
            and any(
                action.kind
                in {
                    ActionKind.CREATE,
                    ActionKind.MOVE,
                    ActionKind.MERGE,
                    ActionKind.DELETE,
                }
                for action in explicit_actions
            )
            else state
        )
    conflicts = tuple(
        f"{action.target_path}: {action.reason}"
        for action in actions
        if action.kind is ActionKind.CONFLICT
    )
    transaction = GovernanceTransaction.build(
        target_root=str(target_root),
        project_state=effective_state,
        template_version=TEMPLATE_VERSION,
        actions=actions,
        conflicts=conflicts,
    )
    write_transaction(transaction, artifact_root)
    return transaction
