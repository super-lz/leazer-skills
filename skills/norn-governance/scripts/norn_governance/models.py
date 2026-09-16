from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping


TRANSACTION_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1


class ProjectState(str, Enum):
    UNINITIALIZED = "uninitialized"
    CURRENT = "current"
    UPGRADEABLE = "upgradeable"
    LEGACY = "legacy"
    MIXED = "mixed"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


class ActionKind(str, Enum):
    CREATE = "create"
    MOVE = "move"
    MERGE = "merge"
    DELETE = "delete"
    KEEP = "keep"
    CONFLICT = "conflict"


class PathKind(str, Enum):
    MISSING = "missing"
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class OwnershipKind(str, Enum):
    MANAGED = "managed"
    MIXED = "mixed"
    PROJECT = "project"


class ConflictChoice(str, Enum):
    KEEP_CURRENT = "keep-current"
    ADOPT_TEMPLATE = "adopt-template"
    SEMANTIC_MERGE = "semantic-merge"


@dataclass(frozen=True)
class ConflictResolution:
    action_id: str
    choice: ConflictChoice
    rendered_path: str | None = None
    rendered_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or not self.action_id:
            raise ValueError("action_id 必须是非空字符串")
        if not isinstance(self.choice, ConflictChoice):
            object.__setattr__(
                self,
                "choice",
                _parse_enum(ConflictChoice, self.choice, "冲突选择"),
            )
        if self.choice is ConflictChoice.SEMANTIC_MERGE:
            if not isinstance(self.rendered_path, str) or not self.rendered_path:
                raise ValueError("semantic-merge 需要 rendered_path")
            _require_sha256(
                self.rendered_sha256, "rendered_sha256", optional=False
            )
        elif self.rendered_path is not None or self.rendered_sha256 is not None:
            raise ValueError(
                "rendered_path 和 rendered_sha256 仅适用于 semantic-merge"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "choice": self.choice.value,
            "rendered_path": self.rendered_path,
            "rendered_sha256": self.rendered_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConflictResolution:
        return cls(
            action_id=payload.get("action_id"),
            choice=_parse_enum(
                ConflictChoice, payload.get("choice"), "冲突选择"
            ),
            rendered_path=payload.get("rendered_path"),
            rendered_sha256=payload.get("rendered_sha256"),
        )


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: str | None, field_name: str, *, optional: bool) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field_name} 必须是 SHA-256 十六进制摘要")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 SHA-256 十六进制摘要") from exc


def _require_nonnegative_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数")


def _require_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是对象")
    return value


def _parse_enum(enum_type: type[Enum], value: object, field_name: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 无效：{value!r}") from exc


@dataclass(frozen=True)
class PathFingerprint:
    exists: bool
    kind: PathKind
    sha256: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PathKind):
            object.__setattr__(self, "kind", _parse_enum(PathKind, self.kind, "路径类型"))
        if self.kind is PathKind.MISSING:
            if self.exists or self.sha256 is not None:
                raise ValueError("missing 指纹不得标记为存在或包含 SHA-256")
            return
        if not self.exists:
            raise ValueError("已存在的路径类型要求 exists=true")
        if self.kind in {PathKind.FILE, PathKind.DIRECTORY}:
            _require_sha256(self.sha256, "sha256", optional=False)
        elif self.sha256 is not None:
            raise ValueError("不支持的路径类型不得包含 SHA-256")

    @classmethod
    def missing(cls) -> PathFingerprint:
        return cls(False, PathKind.MISSING, None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "kind": self.kind.value,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PathFingerprint:
        return cls(
            exists=payload.get("exists"),
            kind=_parse_enum(PathKind, payload.get("kind"), "路径类型"),
            sha256=payload.get("sha256"),
        )


@dataclass(frozen=True)
class TransactionAction:
    action_id: str
    kind: ActionKind
    source_path: str | None
    target_path: str
    source_before: PathFingerprint | None
    target_before: PathFingerprint
    output_sha256: str | None
    ownership: OwnershipKind
    evidence: tuple[str, ...]
    reason: str
    risk: str
    verification: tuple[str, ...]
    allowed_resolutions: tuple[ConflictChoice, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            object.__setattr__(self, "kind", _parse_enum(ActionKind, self.kind, "动作类型"))
        if not isinstance(self.ownership, OwnershipKind):
            object.__setattr__(
                self,
                "ownership",
                _parse_enum(OwnershipKind, self.ownership, "归属类型"),
            )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "verification", tuple(self.verification))
        object.__setattr__(
            self,
            "allowed_resolutions",
            tuple(
                choice
                if isinstance(choice, ConflictChoice)
                else _parse_enum(ConflictChoice, choice, "冲突选择")
                for choice in self.allowed_resolutions
            ),
        )
        if not self.action_id or not isinstance(self.action_id, str):
            raise ValueError("action_id 必须是非空字符串")
        if not self.target_path or not isinstance(self.target_path, str):
            raise ValueError("target_path 必须是非空字符串")
        if self.source_path is not None and self.source_before is None:
            raise ValueError("设置 source_path 时必须提供 source_before")
        if self.source_path is None and self.source_before is not None:
            raise ValueError("提供 source_before 时必须设置 source_path")
        if not isinstance(self.target_before, PathFingerprint):
            raise ValueError("target_before 必须是 PathFingerprint")
        if self.kind in {ActionKind.CREATE, ActionKind.MOVE, ActionKind.MERGE}:
            _require_sha256(self.output_sha256, "output_sha256", optional=False)
        elif self.output_sha256 is not None:
            raise ValueError(f"{self.kind.value} 动作不得包含 output_sha256")
        if self.kind is not ActionKind.CONFLICT and self.allowed_resolutions:
            raise ValueError("allowed_resolutions 仅适用于 conflict 动作")
        if len(set(self.allowed_resolutions)) != len(self.allowed_resolutions):
            raise ValueError("allowed_resolutions 必须唯一")
        for field_name, values in (
            ("evidence", self.evidence),
            ("verification", self.verification),
        ):
            if not all(isinstance(value, str) and value for value in values):
                raise ValueError(f"{field_name} 必须只包含非空字符串")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason 必须是非空字符串")
        if not isinstance(self.risk, str) or not self.risk:
            raise ValueError("risk 必须是非空字符串")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_before": (
                self.source_before.to_dict() if self.source_before is not None else None
            ),
            "target_before": self.target_before.to_dict(),
            "output_sha256": self.output_sha256,
            "ownership": self.ownership.value,
            "evidence": list(self.evidence),
            "reason": self.reason,
            "risk": self.risk,
            "verification": list(self.verification),
            "allowed_resolutions": [choice.value for choice in self.allowed_resolutions],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TransactionAction:
        source_before = payload.get("source_before")
        target_before = _require_mapping(payload.get("target_before"), "target_before")
        return cls(
            action_id=payload.get("action_id"),
            kind=_parse_enum(ActionKind, payload.get("kind"), "动作类型"),
            source_path=payload.get("source_path"),
            target_path=payload.get("target_path"),
            source_before=(
                PathFingerprint.from_dict(
                    _require_mapping(source_before, "source_before")
                )
                if source_before is not None
                else None
            ),
            target_before=PathFingerprint.from_dict(target_before),
            output_sha256=payload.get("output_sha256"),
            ownership=_parse_enum(
                OwnershipKind, payload.get("ownership"), "归属类型"
            ),
            evidence=tuple(payload.get("evidence", ())),
            reason=payload.get("reason"),
            risk=payload.get("risk"),
            verification=tuple(payload.get("verification", ())),
            allowed_resolutions=tuple(
                _parse_enum(ConflictChoice, value, "冲突选择")
                for value in payload.get("allowed_resolutions", ())
            ),
        )


@dataclass(frozen=True)
class GovernanceTransaction:
    transaction_schema_version: int
    target_root: str
    project_state: ProjectState
    template_version: int
    actions: tuple[TransactionAction, ...]
    conflicts: tuple[str, ...]
    transaction_sha256: str

    def __post_init__(self) -> None:
        if self.transaction_schema_version != TRANSACTION_SCHEMA_VERSION:
            raise ValueError(
                f"不支持的治理事务 schema：{self.transaction_schema_version}"
            )
        if not isinstance(self.project_state, ProjectState):
            object.__setattr__(
                self,
                "project_state",
                _parse_enum(ProjectState, self.project_state, "项目状态"),
            )
        _require_nonnegative_integer(self.template_version, "template_version")
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))
        if not isinstance(self.target_root, str) or not Path(self.target_root).is_absolute():
            raise ValueError("target_root 必须是绝对路径")
        if not all(isinstance(action, TransactionAction) for action in self.actions):
            raise ValueError("actions 必须只包含 TransactionAction 值")
        action_ids = [action.action_id for action in self.actions]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("action_id 值必须唯一")
        if not all(isinstance(item, str) and item for item in self.conflicts):
            raise ValueError("conflicts 必须只包含非空字符串")
        _require_sha256(self.transaction_sha256, "transaction_sha256", optional=False)

    @classmethod
    def build(
        cls,
        *,
        target_root: str,
        project_state: ProjectState,
        template_version: int,
        actions: Iterable[TransactionAction],
        conflicts: Iterable[str],
    ) -> GovernanceTransaction:
        actions_tuple = tuple(actions)
        conflicts_tuple = tuple(conflicts)
        payload = cls._payload_without_digest(
            target_root=target_root,
            project_state=project_state,
            template_version=template_version,
            actions=actions_tuple,
            conflicts=conflicts_tuple,
        )
        return cls(
            transaction_schema_version=TRANSACTION_SCHEMA_VERSION,
            target_root=target_root,
            project_state=project_state,
            template_version=template_version,
            actions=actions_tuple,
            conflicts=conflicts_tuple,
            transaction_sha256=sha256_bytes(canonical_json_bytes(payload)),
        )

    @staticmethod
    def _payload_without_digest(
        *,
        target_root: str,
        project_state: ProjectState,
        template_version: int,
        actions: tuple[TransactionAction, ...],
        conflicts: tuple[str, ...],
    ) -> dict[str, Any]:
        state = (
            project_state.value
            if isinstance(project_state, ProjectState)
            else ProjectState(project_state).value
        )
        return {
            "transaction_schema_version": TRANSACTION_SCHEMA_VERSION,
            "target_root": target_root,
            "project_state": state,
            "template_version": template_version,
            "actions": [action.to_dict() for action in actions],
            "conflicts": list(conflicts),
        }

    def expected_digest(self) -> str:
        payload = self._payload_without_digest(
            target_root=self.target_root,
            project_state=self.project_state,
            template_version=self.template_version,
            actions=self.actions,
            conflicts=self.conflicts,
        )
        return sha256_bytes(canonical_json_bytes(payload))

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_without_digest(
            target_root=self.target_root,
            project_state=self.project_state,
            template_version=self.template_version,
            actions=self.actions,
            conflicts=self.conflicts,
        )
        payload["transaction_sha256"] = self.transaction_sha256
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> GovernanceTransaction:
        actions_payload = payload.get("actions")
        if not isinstance(actions_payload, list):
            raise ValueError("actions 必须是数组")
        conflicts_payload = payload.get("conflicts")
        if not isinstance(conflicts_payload, list):
            raise ValueError("conflicts 必须是数组")
        transaction = cls(
            transaction_schema_version=payload.get("transaction_schema_version"),
            target_root=payload.get("target_root"),
            project_state=_parse_enum(
                ProjectState, payload.get("project_state"), "项目状态"
            ),
            template_version=payload.get("template_version"),
            actions=tuple(
                TransactionAction.from_dict(_require_mapping(item, "action"))
                for item in actions_payload
            ),
            conflicts=tuple(conflicts_payload),
            transaction_sha256=payload.get("transaction_sha256"),
        )
        if transaction.transaction_sha256 != transaction.expected_digest():
            raise ValueError("治理事务摘要不匹配")
        return transaction


@dataclass(frozen=True)
class ManagedFileRecord:
    ownership: OwnershipKind
    base_sha256: str | None
    managed_blocks: tuple[str, ...]
    template_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, OwnershipKind):
            object.__setattr__(
                self,
                "ownership",
                _parse_enum(OwnershipKind, self.ownership, "归属类型"),
            )
        object.__setattr__(self, "managed_blocks", tuple(self.managed_blocks))
        _require_nonnegative_integer(self.template_version, "template_version")
        if len(set(self.managed_blocks)) != len(self.managed_blocks):
            raise ValueError("managed_blocks 必须唯一")
        if not all(
            isinstance(block_id, str) and block_id for block_id in self.managed_blocks
        ):
            raise ValueError("managed_blocks 必须只包含非空字符串")
        if self.ownership is OwnershipKind.PROJECT:
            if self.base_sha256 is not None or self.managed_blocks:
                raise ValueError(
                    "项目自有记录不得定义 base_sha256 或 managed_blocks"
                )
        else:
            _require_sha256(self.base_sha256, "base_sha256", optional=False)
            if not self.managed_blocks:
                raise ValueError("受管归属必须定义 managed_blocks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ownership": self.ownership.value,
            "base_sha256": self.base_sha256,
            "managed_blocks": list(self.managed_blocks),
            "template_version": self.template_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ManagedFileRecord:
        return cls(
            ownership=_parse_enum(
                OwnershipKind, payload.get("ownership"), "归属类型"
            ),
            base_sha256=payload.get("base_sha256"),
            managed_blocks=tuple(payload.get("managed_blocks", ())),
            template_version=payload.get("template_version"),
        )


@dataclass(frozen=True)
class NornManifest:
    schema_version: int
    template_version: int
    managed_files: Mapping[str, ManagedFileRecord]

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"不支持的 manifest schema：{self.schema_version}")
        _require_nonnegative_integer(self.template_version, "template_version")
        normalized: dict[str, ManagedFileRecord] = {}
        for path, record in sorted(self.managed_files.items()):
            if not isinstance(path, str) or not path:
                raise ValueError("受管文件路径必须是非空字符串")
            if not isinstance(record, ManagedFileRecord):
                raise ValueError("managed_files 的值必须是 ManagedFileRecord")
            normalized[path] = record
        object.__setattr__(self, "managed_files", MappingProxyType(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "template_version": self.template_version,
            "managed_files": {
                path: record.to_dict() for path, record in self.managed_files.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> NornManifest:
        managed_files = _require_mapping(payload.get("managed_files"), "managed_files")
        return cls(
            schema_version=payload.get("schema_version"),
            template_version=payload.get("template_version"),
            managed_files={
                path: ManagedFileRecord.from_dict(
                    _require_mapping(record, f"managed_files[{path!r}]")
                )
                for path, record in managed_files.items()
            },
        )


def write_transaction(transaction: GovernanceTransaction, directory: Path) -> Path:
    if transaction.transaction_sha256 != transaction.expected_digest():
        raise ValueError("治理事务摘要不匹配")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    transaction_path = directory / "transaction.json"
    payload = json.dumps(
        transaction.to_dict(), ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".transaction.", suffix=".tmp", dir=directory
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, transaction_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return transaction_path


def load_transaction(path: Path) -> GovernanceTransaction:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"治理事务文件无效：{exc}") from exc
    return GovernanceTransaction.from_dict(_require_mapping(payload, "transaction"))
