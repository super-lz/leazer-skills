from __future__ import annotations

import json
import hashlib
import shutil
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from norn_governance.models import (  # noqa: E402
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
    load_transaction,
    write_transaction,
)
from norn_governance.analyzer import (  # noqa: E402
    analyze_governance,
    classify_project,
    fingerprint_path,
    resolve_conflicts,
)
from norn_governance.executor import apply_transaction  # noqa: E402
from norn_governance.templates import (  # noqa: E402
    MANAGED_PATHS,
    TEMPLATE_VERSION,
    asset_template_root,
)
from norn_governance.managed_markdown import (  # noqa: E402
    parse_managed_blocks,
    replace_managed_block,
)


class TransactionModelTests(unittest.TestCase):
    def make_action(self) -> TransactionAction:
        return TransactionAction(
            action_id="create-root-agents",
            kind=ActionKind.CREATE,
            source_path=None,
            target_path="AGENTS.md",
            source_before=None,
            target_before=PathFingerprint.missing(),
            output_sha256="a" * 64,
            ownership=OwnershipKind.MANAGED,
            evidence=("target path is missing",),
            reason="initialize Norn entrypoint",
            risk="creates a new file",
            verification=("target SHA-256 equals transaction output",),
            allowed_resolutions=(),
        )

    def make_transaction(self) -> GovernanceTransaction:
        return GovernanceTransaction.build(
            target_root="/tmp/example",
            project_state=ProjectState.UNINITIALIZED,
            template_version=1,
            actions=(self.make_action(),),
            conflicts=(),
        )

    def test_transaction_digest_is_stable_and_excludes_its_own_digest(self) -> None:
        first = self.make_transaction()
        second = self.make_transaction()

        self.assertEqual(len(first.transaction_sha256), 64)
        self.assertEqual(first.transaction_sha256, second.transaction_sha256)
        self.assertEqual(first.to_dict()["transaction_sha256"], first.transaction_sha256)
        self.assertEqual(
            GovernanceTransaction.from_dict(first.to_dict()).to_dict(), first.to_dict()
        )

    def test_tampered_transaction_digest_is_rejected(self) -> None:
        payload = self.make_transaction().to_dict()
        payload["template_version"] = 2

        with self.assertRaisesRegex(ValueError, "治理事务摘要不匹配"):
            GovernanceTransaction.from_dict(payload)

    def test_transaction_json_uses_enum_values_and_immutable_collections(self) -> None:
        transaction = self.make_transaction()
        payload = transaction.to_dict()

        self.assertEqual(payload["project_state"], "uninitialized")
        self.assertEqual(payload["actions"][0]["kind"], "create")
        self.assertEqual(payload["actions"][0]["ownership"], "managed")
        self.assertIsInstance(transaction.actions, tuple)
        self.assertIsInstance(transaction.actions[0].evidence, tuple)
        with self.assertRaises(FrozenInstanceError):
            transaction.template_version = 2  # type: ignore[misc]

    def test_path_fingerprints_distinguish_missing_file_and_directory(self) -> None:
        missing = PathFingerprint.missing()
        file_path = PathFingerprint(True, PathKind.FILE, "b" * 64)
        directory = PathFingerprint(True, PathKind.DIRECTORY, "c" * 64)

        self.assertEqual(missing.to_dict(), {"exists": False, "kind": "missing", "sha256": None})
        self.assertEqual(file_path.to_dict()["kind"], "file")
        self.assertEqual(directory.to_dict()["kind"], "directory")

    def test_write_and_load_transaction_round_trip_and_reject_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transaction_path = write_transaction(self.make_transaction(), Path(directory))
            self.assertEqual(transaction_path.name, "transaction.json")
            self.assertEqual(load_transaction(transaction_path).to_dict(), self.make_transaction().to_dict())

            payload = json.loads(transaction_path.read_text(encoding="utf-8"))
            payload["project_state"] = "current"
            transaction_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "治理事务摘要不匹配"):
                load_transaction(transaction_path)

    def test_write_rejects_transaction_with_invalid_digest(self) -> None:
        invalid = replace(self.make_transaction(), transaction_sha256="f" * 64)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "治理事务摘要不匹配"):
                write_transaction(invalid, Path(directory))
            self.assertFalse((Path(directory) / "transaction.json").exists())


class ManifestModelTests(unittest.TestCase):
    def make_manifest(self) -> NornManifest:
        return NornManifest(
            schema_version=1,
            template_version=1,
            managed_files={
                "AGENTS.md": ManagedFileRecord(
                    ownership=OwnershipKind.MIXED,
                    base_sha256="d" * 64,
                    managed_blocks=("core-governance",),
                    template_version=1,
                ),
                "norn-governance/spec/main-spec.md": ManagedFileRecord(
                    ownership=OwnershipKind.PROJECT,
                    base_sha256=None,
                    managed_blocks=(),
                    template_version=1,
                ),
            },
        )

    def test_manifest_round_trip_is_sorted_and_immutable(self) -> None:
        manifest = self.make_manifest()
        restored = NornManifest.from_dict(manifest.to_dict())

        self.assertEqual(restored.to_dict(), manifest.to_dict())
        self.assertEqual(list(manifest.managed_files), sorted(manifest.managed_files))
        with self.assertRaises(TypeError):
            manifest.managed_files["other.md"] = manifest.managed_files["AGENTS.md"]  # type: ignore[index]

    def test_manifest_rejects_invalid_contract_values(self) -> None:
        valid = self.make_manifest().to_dict()
        cases = [
            ({**valid, "schema_version": 2}, "不支持的 manifest schema"),
            ({**valid, "template_version": -1}, "template_version"),
            (
                {
                    **valid,
                    "managed_files": {
                        **valid["managed_files"],
                        "AGENTS.md": {
                            **valid["managed_files"]["AGENTS.md"],
                            "ownership": "unknown",
                        },
                    },
                },
                "归属类型",
            ),
            (
                {
                    **valid,
                    "managed_files": {
                        **valid["managed_files"],
                        "AGENTS.md": {
                            **valid["managed_files"]["AGENTS.md"],
                            "base_sha256": "short",
                        },
                    },
                },
                "base_sha256",
            ),
            (
                {
                    **valid,
                    "managed_files": {
                        **valid["managed_files"],
                        "AGENTS.md": {
                            **valid["managed_files"]["AGENTS.md"],
                            "managed_blocks": [],
                        },
                    },
                },
                "managed_blocks",
            ),
        ]

        for payload, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    NornManifest.from_dict(payload)

    def test_resolvable_conflict_preserves_explicit_allowed_choices(self) -> None:
        action = self.make_conflict_action()

        self.assertEqual(
            action.allowed_resolutions,
            (ConflictChoice.ADOPT_TEMPLATE, ConflictChoice.SEMANTIC_MERGE),
        )

    def test_blocking_conflict_can_require_external_change_and_reanalysis(self) -> None:
        action = replace(self.make_conflict_action(), allowed_resolutions=())

        self.assertEqual(action.kind, ActionKind.CONFLICT)
        self.assertEqual(action.allowed_resolutions, ())

    def make_conflict_action(self) -> TransactionAction:
        return TransactionAction(
            action_id="merge-root-agents",
            kind=ActionKind.CONFLICT,
            source_path=None,
            target_path="AGENTS.md",
            source_before=None,
            target_before=PathFingerprint(True, PathKind.FILE, "e" * 64),
            output_sha256=None,
            ownership=OwnershipKind.MIXED,
            evidence=("managed block differs",),
            reason="requires semantic choice",
            risk="project rules could be lost",
            verification=("choice is represented in a resolved transaction",),
            allowed_resolutions=(
                ConflictChoice.ADOPT_TEMPLATE,
                ConflictChoice.SEMANTIC_MERGE,
            ),
        )


class GovernanceAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        self.asset_root = asset_template_root()
        self.legacy_root = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "legacy-templates"
            / "0"
        )
        self.v3_root_agents = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "template-v3"
            / "AGENTS.md"
        )
        self.v4_template_root = (
            Path(__file__).resolve().parent / "fixtures" / "template-v4"
        )
        self.artifact_counter = 0

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_target(self) -> Path:
        target = self.workspace / f"target-{len(tuple(self.workspace.glob('target-*')))}"
        target.mkdir()
        return target

    def artifacts(self) -> Path:
        self.artifact_counter += 1
        return self.workspace / f"artifacts-{self.artifact_counter}"

    def write(self, target: Path, relative_path: str, text: str) -> None:
        path = target / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, target: Path, relative_path: str, text: str) -> None:
        path = target / relative_path
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def copy_current_template(self) -> Path:
        target = self.make_target()
        shutil.copytree(self.asset_root, target, dirs_exist_ok=True)
        return target

    def copy_legacy_template(self) -> Path:
        target = self.make_target()
        shutil.copytree(self.legacy_root, target, dirs_exist_ok=True)
        return target

    def copy_versioned_project(self) -> Path:
        target = self.copy_current_template()
        root_path = target / "AGENTS.md"
        old_block = (
            "<!-- norn:managed:start core-governance -->\n"
            "# Prior Core Governance\n\nLegacy managed rules.\n"
            "<!-- norn:managed:end core-governance -->"
        )
        root_path.write_text(
            replace_managed_block(
                root_path.read_text(encoding="utf-8"),
                "core-governance",
                old_block,
            ),
            encoding="utf-8",
        )
        manifest_path = target / "norn-governance/.norn.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["template_version"] = 0
        for record in manifest["managed_files"].values():
            record["template_version"] = 0
        manifest["managed_files"]["AGENTS.md"]["base_sha256"] = hashlib.sha256(
            old_block.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    def copy_v3_template(self) -> Path:
        target = self.copy_current_template()
        root_path = target / "AGENTS.md"
        root_path.write_bytes(self.v3_root_agents.read_bytes())
        previous_block = parse_managed_blocks(
            root_path.read_text(encoding="utf-8")
        )["core-governance"]
        manifest_path = target / "norn-governance/.norn.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["template_version"] = 3
        for record in manifest["managed_files"].values():
            record["template_version"] = 3
        manifest["managed_files"]["AGENTS.md"][
            "base_sha256"
        ] = previous_block.sha256
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    def copy_v4_template(self) -> Path:
        target = self.make_target()
        shutil.copytree(self.v4_template_root, target, dirs_exist_ok=True)
        return target

    def snapshot(self, target: Path) -> dict[str, bytes]:
        return {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }

    def action_for(
        self,
        transaction: GovernanceTransaction,
        target_path: str,
        kind: ActionKind | None = None,
    ) -> TransactionAction:
        matches = [
            action
            for action in transaction.actions
            if action.target_path == target_path
            and (kind is None or action.kind is kind)
        ]
        self.assertEqual(len(matches), 1, (target_path, kind, transaction.to_dict()))
        return matches[0]

    def rendered_text(self, artifacts: Path, action: TransactionAction) -> str:
        path = artifacts / "rendered" / f"{action.action_id}.content"
        body = path.read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), action.output_sha256)
        return body.decode("utf-8")

    def test_fingerprint_path_distinguishes_supported_path_kinds(self) -> None:
        target = self.make_target()
        file_path = target / "file.md"
        file_path.write_text("content\n", encoding="utf-8")
        directory_path = target / "directory"
        directory_path.mkdir()
        (directory_path / "child.txt").write_text("child\n", encoding="utf-8")

        self.assertEqual(fingerprint_path(target / "missing").kind, PathKind.MISSING)
        self.assertEqual(
            fingerprint_path(file_path).sha256,
            hashlib.sha256(b"content\n").hexdigest(),
        )
        first_directory_hash = fingerprint_path(directory_path).sha256
        (directory_path / "second.txt").write_text("second\n", encoding="utf-8")
        self.assertNotEqual(fingerprint_path(directory_path).sha256, first_directory_hash)

    def test_empty_project_is_uninitialized_and_analysis_is_read_only(self) -> None:
        target = self.make_target()
        artifacts = self.artifacts()
        before = self.snapshot(target)

        transaction = analyze_governance(target, artifacts)

        self.assertEqual(transaction.project_state, ProjectState.UNINITIALIZED)
        self.assertEqual(self.snapshot(target), before)
        self.assertEqual(
            {
                action.target_path
                for action in transaction.actions
                if action.kind is ActionKind.CREATE
            },
            {*MANAGED_PATHS, "norn-governance/.norn.json"},
        )
        self.assertEqual(load_transaction(artifacts / "transaction.json").to_dict(), transaction.to_dict())

    def test_custom_root_can_resolve_initialization_without_missing_actions(self) -> None:
        target = self.make_target()
        self.write(target, "AGENTS.md", "# Existing project rules\n\nKeep this.\n")
        artifacts = self.artifacts()
        original = analyze_governance(target, artifacts)
        conflict = self.action_for(
            original,
            "AGENTS.md",
            ActionKind.CONFLICT,
        )
        semantic_path = artifacts / "semantic-input.md"
        semantic_body = (
            (self.asset_root / "AGENTS.md").read_text(encoding="utf-8")
            + "\n# Existing project rules\n\nKeep this.\n"
        ).encode("utf-8")
        semantic_path.write_bytes(semantic_body)

        resolved = resolve_conflicts(
            original,
            (
                ConflictResolution(
                    action_id=conflict.action_id,
                    choice=ConflictChoice.SEMANTIC_MERGE,
                    rendered_path=str(semantic_path),
                    rendered_sha256=hashlib.sha256(semantic_body).hexdigest(),
                ),
            ),
            artifacts,
        )

        self.assertFalse(resolved.conflicts)
        self.assertEqual(
            {
                action.target_path
                for action in resolved.actions
                if action.kind in {ActionKind.CREATE, ActionKind.MERGE}
            },
            {*MANAGED_PATHS, "norn-governance/.norn.json"},
        )

    def test_current_project_has_only_keep_actions(self) -> None:
        target = self.copy_current_template()

        self.assertEqual(classify_project(target), ProjectState.CURRENT)
        transaction = analyze_governance(target, self.artifacts())

        self.assertEqual(transaction.project_state, ProjectState.CURRENT)
        self.assertTrue(transaction.actions)
        self.assertTrue(all(action.kind is ActionKind.KEEP for action in transaction.actions))

    def test_isolated_legacy_named_spec_is_ambiguous(self) -> None:
        target = self.make_target()
        self.write(target, "docs/spec/main-spec.md", "# Existing product spec\n")

        transaction = analyze_governance(target, self.artifacts())

        self.assertEqual(transaction.project_state, ProjectState.AMBIGUOUS)
        self.assertEqual(transaction.actions[0].kind, ActionKind.CONFLICT)
        self.assertIn("旧内容归属证据不足", transaction.actions[0].reason)

    def test_partial_exact_legacy_files_are_mixed_not_ambiguous(self) -> None:
        target = self.make_target()
        source = self.legacy_root / "docs/spec/main-spec.md"
        destination = target / "docs/spec/main-spec.md"
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)

        transaction = analyze_governance(target, self.artifacts())

        self.assertEqual(transaction.project_state, ProjectState.MIXED)

    def test_complete_legacy_bundle_builds_hashed_merge_artifacts(self) -> None:
        target = self.copy_legacy_template()
        artifacts = self.artifacts()
        before = self.snapshot(target)

        transaction = analyze_governance(target, artifacts)

        self.assertEqual(transaction.project_state, ProjectState.LEGACY)
        self.assertEqual(self.snapshot(target), before)
        self.assertEqual(
            {
                (action.source_path, action.target_path, action.kind)
                for action in transaction.actions
                if action.source_path
            },
            {
                (
                    "docs/AGENTS.md",
                    "norn-governance/AGENTS.md",
                    ActionKind.MERGE,
                ),
                (
                    "docs/spec/AGENTS.md",
                    "norn-governance/spec/AGENTS.md",
                    ActionKind.MERGE,
                ),
                (
                    "docs/spec/main-spec.md",
                    "norn-governance/spec/main-spec.md",
                    ActionKind.MERGE,
                ),
                (
                    "docs/appendix/README.md",
                    "norn-governance/appendix/README.md",
                    ActionKind.MERGE,
                ),
            },
        )
        for action in transaction.actions:
            if action.output_sha256:
                self.rendered_text(artifacts, action)

    def test_customized_main_spec_and_other_docs_are_preserved(self) -> None:
        target = self.copy_legacy_template()
        self.append(
            target,
            "docs/spec/main-spec.md",
            "\n## Business Contract\nOrder state remains durable.\n",
        )
        self.write(target, "docs/architecture.md", "project-owned\n")
        artifacts = self.artifacts()

        transaction = analyze_governance(target, artifacts)

        self.assertEqual(transaction.project_state, ProjectState.LEGACY)
        spec_action = self.action_for(
            transaction, "norn-governance/spec/main-spec.md", ActionKind.MERGE
        )
        rendered = self.rendered_text(artifacts, spec_action)
        self.assertIn("Order state remains durable.", rendered)
        self.assertIn("norn-governance/spec/AGENTS.md", rendered)
        self.assertNotIn(
            "docs/architecture.md",
            {
                path
                for action in transaction.actions
                for path in (action.source_path, action.target_path)
                if path
            },
        )
        self.assertFalse(
            any(
                action.kind is ActionKind.DELETE and action.target_path == "docs"
                for action in transaction.actions
            )
        )

    def test_customized_legacy_governance_requires_semantic_choice(self) -> None:
        target = self.copy_legacy_template()
        self.append(target, "docs/AGENTS.md", "\n## Project Rule\nKeep this.\n")

        transaction = analyze_governance(target, self.artifacts())

        self.assertEqual(transaction.project_state, ProjectState.CONFLICT)
        action = self.action_for(transaction, "norn-governance/AGENTS.md")
        self.assertEqual(action.kind, ActionKind.CONFLICT)
        self.assertEqual(
            action.allowed_resolutions,
            (ConflictChoice.ADOPT_TEMPLATE, ConflictChoice.SEMANTIC_MERGE),
        )
        self.assertFalse(
            any(
                action.target_path == "norn-governance/.norn.json"
                for action in transaction.actions
            )
        )

    def test_equal_destination_is_kept_and_duplicate_source_is_deleted(self) -> None:
        target = self.copy_legacy_template()
        destination = target / "norn-governance/AGENTS.md"
        destination.parent.mkdir(parents=True)
        shutil.copy2(self.asset_root / "norn-governance/AGENTS.md", destination)

        transaction = analyze_governance(target, self.artifacts())

        self.action_for(transaction, "norn-governance/AGENTS.md", ActionKind.KEEP)
        duplicate_delete = self.action_for(transaction, "docs/AGENTS.md", ActionKind.DELETE)
        self.assertEqual(duplicate_delete.output_sha256, None)

    def test_explicit_tree_reports_target_parent_collision_during_analysis(
        self,
    ) -> None:
        """防止嵌套目标父路径为文件时生成表面可执行、实际必失败的事务。"""
        target = self.copy_current_template()
        self.write(target, "docs/appendix/diagrams/flow.md", "# Flow\n")
        self.write(
            target,
            "norn-governance/appendix/diagrams",
            "this path blocks the destination directory\n",
        )

        transaction = analyze_governance(
            target,
            self.artifacts(),
            legacy_content_scopes=("appendix",),
        )

        action = self.action_for(
            transaction,
            "norn-governance/appendix/diagrams/flow.md",
        )
        self.assertEqual(action.kind, ActionKind.CONFLICT)
        self.assertIn("父路径", action.reason)
        self.assertFalse(action.allowed_resolutions)

    def test_explicit_tree_does_not_overwrite_different_project_content(self) -> None:
        target = self.copy_current_template()
        self.write(target, "docs/appendix/guide.md", "legacy guide\n")
        self.write(
            target,
            "norn-governance/appendix/guide.md",
            "current guide\n",
        )

        transaction = analyze_governance(
            target,
            self.artifacts(),
            legacy_content_scopes=("appendix",),
        )

        action = self.action_for(transaction, "norn-governance/appendix/guide.md")
        self.assertEqual(action.kind, ActionKind.CONFLICT)
        self.assertEqual(
            action.allowed_resolutions,
            (ConflictChoice.SEMANTIC_MERGE,),
        )
        self.assertEqual(
            (target / "norn-governance/appendix/guide.md").read_text(
                encoding="utf-8"
            ),
            "current guide\n",
        )

    def test_explicit_tree_blocks_symlinked_source(self) -> None:
        target = self.copy_current_template()
        outside = self.workspace / "outside-appendix.md"
        outside.write_text("outside\n", encoding="utf-8")
        source = target / "docs/appendix/external.md"
        source.parent.mkdir(parents=True)
        source.symlink_to(outside)

        transaction = analyze_governance(
            target,
            self.artifacts(),
            legacy_content_scopes=("appendix",),
        )

        action = self.action_for(transaction, "norn-governance/appendix/external.md")
        self.assertEqual(action.kind, ActionKind.CONFLICT)
        self.assertIn("不支持的文件系统类型", action.reason)
        self.assertFalse(action.allowed_resolutions)

    def test_explicit_empty_legacy_tree_is_reported_as_mixed_cleanup(self) -> None:
        """防止只剩空旧目录时把删除事务误报为 current 无变更。"""
        target = self.copy_current_template()
        (target / "docs/appendix").mkdir(parents=True)

        transaction = analyze_governance(
            target,
            self.artifacts(),
            legacy_content_scopes=("appendix",),
        )

        self.assertEqual(transaction.project_state, ProjectState.MIXED)
        self.action_for(transaction, "docs/appendix", ActionKind.DELETE)
        self.action_for(transaction, "docs", ActionKind.DELETE)

    def test_current_and_legacy_paths_are_mixed(self) -> None:
        target = self.copy_current_template()
        legacy = self.legacy_root / "docs/spec/main-spec.md"
        destination = target / "docs/spec/main-spec.md"
        destination.parent.mkdir(parents=True)
        shutil.copy2(legacy, destination)

        self.assertEqual(classify_project(target), ProjectState.MIXED)

    def test_current_root_with_exact_legacy_bundle_is_recoverable_mixed_state(self) -> None:
        target = self.copy_legacy_template()
        shutil.copy2(self.asset_root / "AGENTS.md", target / "AGENTS.md")

        transaction = analyze_governance(target, self.artifacts())

        self.assertEqual(transaction.project_state, ProjectState.MIXED)
        self.assertFalse(transaction.conflicts)
        self.assertEqual(
            self.action_for(transaction, "AGENTS.md").kind,
            ActionKind.KEEP,
        )

    def test_future_manifest_and_directory_at_file_path_are_conflicts(self) -> None:
        future = self.copy_current_template()
        manifest_path = future / "norn-governance/.norn.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["template_version"] = 99
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        directory_conflict = self.make_target()
        (directory_conflict / "AGENTS.md").mkdir()

        self.assertEqual(classify_project(future), ProjectState.CONFLICT)
        self.assertEqual(
            classify_project(directory_conflict), ProjectState.CONFLICT
        )

    def test_manifest_record_version_must_match_manifest_version(self) -> None:
        target = self.copy_current_template()
        manifest_path = target / "norn-governance/.norn.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["managed_files"]["AGENTS.md"]["template_version"] = 0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.assertEqual(classify_project(target), ProjectState.CONFLICT)

    def test_symlinked_governance_parent_is_a_conflict(self) -> None:
        target = self.make_target()
        shutil.copy2(self.asset_root / "AGENTS.md", target / "AGENTS.md")
        os.symlink(
            self.asset_root / "norn-governance",
            target / "norn-governance",
            target_is_directory=True,
        )

        self.assertEqual(classify_project(target), ProjectState.CONFLICT)

    def test_unmodified_managed_block_upgrades_and_preserves_project_text(self) -> None:
        target = self.copy_versioned_project()
        self.append(target, "AGENTS.md", "\n## Project Rule\nKeep this.\n")
        artifacts = self.artifacts()

        transaction = analyze_governance(target, artifacts)

        self.assertEqual(transaction.project_state, ProjectState.UPGRADEABLE)
        root_action = self.action_for(transaction, "AGENTS.md", ActionKind.MERGE)
        rendered = self.rendered_text(artifacts, root_action)
        self.assertIn("## Project Rule\nKeep this.", rendered)
        self.assertIn("## 开发生命周期", rendered)
        manifest_action = self.action_for(
            transaction, "norn-governance/.norn.json", ActionKind.MERGE
        )
        upgraded_manifest = NornManifest.from_dict(
            json.loads(self.rendered_text(artifacts, manifest_action))
        )
        self.assertEqual(upgraded_manifest.template_version, TEMPLATE_VERSION)

    def test_v3_upgrade_adds_spec_notices_deterministically_and_preserves_project_text(
        self,
    ) -> None:
        self.assertEqual(TEMPLATE_VERSION, 5)
        target = self.copy_v3_template()
        project_text = "\n## Project Rule\nKeep this byte-for-byte.\n"
        self.append(target, "AGENTS.md", project_text)
        main_spec_before = (
            target / "norn-governance/spec/main-spec.md"
        ).read_bytes()
        first_artifacts = self.artifacts()
        second_artifacts = self.artifacts()

        first = analyze_governance(target, first_artifacts)
        second = analyze_governance(target, second_artifacts)

        self.assertEqual(first.project_state, ProjectState.UPGRADEABLE)
        self.assertEqual(first.to_dict(), second.to_dict())
        root_action = self.action_for(first, "AGENTS.md", ActionKind.MERGE)
        rendered = self.rendered_text(first_artifacts, root_action)
        self.assertIn("`🚨`", rendered)
        self.assertIn("`⚠️`", rendered)
        self.assertIn("不得输出 `🔵`", rendered)
        self.assertTrue(rendered.endswith(project_text))

        result = apply_transaction(first_artifacts / "transaction.json")

        self.assertEqual(result.verification.state, ProjectState.CURRENT)
        upgraded = (target / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(upgraded.endswith(project_text))
        self.assertIn("`🚨`", upgraded)
        self.assertIn("`⚠️`", upgraded)
        self.assertEqual(
            (target / "norn-governance/spec/main-spec.md").read_bytes(),
            main_spec_before,
        )

    def test_v4_upgrade_translates_all_managed_blocks_and_preserves_project_bytes(
        self,
    ) -> None:
        target = self.copy_v4_template()
        project_suffixes = {
            "AGENTS.md": b"\n\n# Project root customization\nKeep root bytes.\n",
            "norn-governance/AGENTS.md": b"\n\n# Project governance note\nKeep directory bytes.\n",
            "norn-governance/spec/AGENTS.md": b"\n\n# Project spec note\nKeep spec bytes.\n",
            "norn-governance/appendix/README.md": b"\n\n# Project appendix note\nKeep appendix bytes.\n",
        }
        for relative_path, suffix in project_suffixes.items():
            path = target / relative_path
            path.write_bytes(path.read_bytes() + suffix)
        project_spec = target / "norn-governance/spec/main-spec.md"
        project_spec.write_bytes(b"# Project-owned specification\nDo not translate this.\n")
        main_spec_before = project_spec.read_bytes()
        first_artifacts = self.artifacts()
        second_artifacts = self.artifacts()

        first = analyze_governance(target, first_artifacts)
        second = analyze_governance(target, second_artifacts)

        self.assertEqual(first.project_state, ProjectState.UPGRADEABLE)
        self.assertEqual(first.template_version, 5)
        self.assertEqual(first.to_dict(), second.to_dict())
        expected_phrases = {
            "AGENTS.md": "Norn 协作约定",
            "norn-governance/AGENTS.md": "Norn 治理目录",
            "norn-governance/spec/AGENTS.md": "主规格治理",
            "norn-governance/appendix/README.md": "附录",
        }
        for relative_path, phrase in expected_phrases.items():
            action = self.action_for(first, relative_path, ActionKind.MERGE)
            rendered = (first_artifacts / "rendered" / f"{action.action_id}.content").read_bytes()
            self.assertIn(phrase, rendered.decode("utf-8"))
            self.assertTrue(rendered.endswith(project_suffixes[relative_path]))

        result = apply_transaction(first_artifacts / "transaction.json")

        self.assertEqual(result.verification.state, ProjectState.CURRENT)
        for relative_path, suffix in project_suffixes.items():
            self.assertTrue((target / relative_path).read_bytes().endswith(suffix))
        self.assertEqual(project_spec.read_bytes(), main_spec_before)

    def test_v4_fixture_matches_published_managed_block_hashes(self) -> None:
        expected_hashes = {
            "AGENTS.md": "173c74b21abd025eb3938bbe2206028618c30650d9d2938ec327265b834bb422",
            "norn-governance/AGENTS.md": "7ec0045c62bca80ac16e8e5a744a83e43407ade32767b2e4f736151b4b5b745c",
            "norn-governance/spec/AGENTS.md": "4d8dd783bf21c62c9f9a0bb1d6e0afd8fa6be60315e0571647507972edd73e9c",
            "norn-governance/appendix/README.md": "1cee5eb879d04826b11114d6a0001ed1a63fbfb533c121d1c9405bdb3bade3a0",
        }

        for relative_path, expected_hash in expected_hashes.items():
            with self.subTest(path=relative_path):
                blocks = parse_managed_blocks(
                    (self.v4_template_root / relative_path).read_text(encoding="utf-8")
                )
                self.assertEqual(next(iter(blocks.values())).sha256, expected_hash)

    def test_modified_managed_block_requires_explicit_choice(self) -> None:
        target = self.copy_v4_template()
        root_path = target / "AGENTS.md"
        customized = (
            "<!-- norn:managed:start core-governance -->\n"
            "project customized managed text\n"
            "<!-- norn:managed:end core-governance -->"
        )
        root_path.write_text(
            replace_managed_block(
                root_path.read_text(encoding="utf-8"),
                "core-governance",
                customized,
            ),
            encoding="utf-8",
        )

        transaction = analyze_governance(target, self.artifacts())

        root_action = self.action_for(transaction, "AGENTS.md", ActionKind.CONFLICT)
        self.assertIn("受管区块与记录的基线不同", root_action.reason)
        self.assertEqual(
            root_action.allowed_resolutions,
            (
                ConflictChoice.KEEP_CURRENT,
                ConflictChoice.ADOPT_TEMPLATE,
                ConflictChoice.SEMANTIC_MERGE,
            ),
        )

    def test_keep_current_resolves_upgrade_and_records_new_baseline(self) -> None:
        target = self.copy_versioned_project()
        root_path = target / "AGENTS.md"
        customized = (
            "<!-- norn:managed:start core-governance -->\ncustom baseline\n"
            "<!-- norn:managed:end core-governance -->"
        )
        root_path.write_text(
            replace_managed_block(
                root_path.read_text(encoding="utf-8"),
                "core-governance",
                customized,
            ),
            encoding="utf-8",
        )
        artifacts = self.artifacts()
        original = analyze_governance(target, artifacts)
        conflict = self.action_for(original, "AGENTS.md", ActionKind.CONFLICT)

        resolved = resolve_conflicts(
            original,
            (
                ConflictResolution(
                    action_id=conflict.action_id,
                    choice=ConflictChoice.KEEP_CURRENT,
                ),
            ),
            artifacts,
        )

        self.assertNotEqual(resolved.transaction_sha256, original.transaction_sha256)
        self.assertFalse(resolved.conflicts)
        self.action_for(resolved, "AGENTS.md", ActionKind.KEEP)
        manifest_action = self.action_for(
            resolved, "norn-governance/.norn.json", ActionKind.MERGE
        )
        manifest = NornManifest.from_dict(
            json.loads(self.rendered_text(artifacts, manifest_action))
        )
        self.assertEqual(
            manifest.managed_files["AGENTS.md"].base_sha256,
            parse_managed_blocks(root_path.read_text(encoding="utf-8"))[
                "core-governance"
            ].sha256,
        )

    def test_adopt_template_resolves_customized_legacy_governance(self) -> None:
        target = self.copy_legacy_template()
        self.append(target, "docs/AGENTS.md", "\n## Custom Legacy Rule\n")
        artifacts = self.artifacts()
        original = analyze_governance(target, artifacts)
        conflict = self.action_for(
            original, "norn-governance/AGENTS.md", ActionKind.CONFLICT
        )

        resolved = resolve_conflicts(
            original,
            (
                ConflictResolution(
                    action_id=conflict.action_id,
                    choice=ConflictChoice.ADOPT_TEMPLATE,
                ),
            ),
            artifacts,
        )

        adopted = self.action_for(
            resolved, "norn-governance/AGENTS.md", ActionKind.MERGE
        )
        self.assertEqual(
            self.rendered_text(artifacts, adopted).encode("utf-8"),
            (self.asset_root / "norn-governance/AGENTS.md").read_bytes(),
        )
        self.action_for(resolved, "norn-governance/.norn.json")

    def test_semantic_merge_is_canonicalized_and_hash_bound(self) -> None:
        target = self.copy_legacy_template()
        self.append(target, "docs/AGENTS.md", "\n## Custom Legacy Rule\nKeep this.\n")
        artifacts = self.artifacts()
        original = analyze_governance(target, artifacts)
        conflict = self.action_for(
            original, "norn-governance/AGENTS.md", ActionKind.CONFLICT
        )
        semantic_path = artifacts / "semantic-input.md"
        semantic_body = (
            (self.asset_root / "norn-governance/AGENTS.md").read_text(
                encoding="utf-8"
            )
            + "\n## Custom Legacy Rule\nKeep this.\n"
        ).encode("utf-8")
        semantic_path.write_bytes(semantic_body)
        semantic_sha256 = hashlib.sha256(semantic_body).hexdigest()

        resolved = resolve_conflicts(
            original,
            (
                ConflictResolution(
                    action_id=conflict.action_id,
                    choice=ConflictChoice.SEMANTIC_MERGE,
                    rendered_path=str(semantic_path),
                    rendered_sha256=semantic_sha256,
                ),
            ),
            artifacts,
        )

        action = self.action_for(
            resolved, "norn-governance/AGENTS.md", ActionKind.MERGE
        )
        self.assertEqual(action.output_sha256, semantic_sha256)
        self.assertEqual(
            (artifacts / "rendered" / f"{action.action_id}.content").read_bytes(),
            semantic_body,
        )

    def test_semantic_merge_rejects_outside_or_mismatched_artifact(self) -> None:
        target = self.copy_legacy_template()
        self.append(target, "docs/AGENTS.md", "\n## Custom Legacy Rule\n")
        artifacts = self.artifacts()
        original = analyze_governance(target, artifacts)
        conflict = self.action_for(
            original, "norn-governance/AGENTS.md", ActionKind.CONFLICT
        )
        outside = self.workspace / "outside.md"
        outside.write_text("unsafe\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "产物根目录"):
            resolve_conflicts(
                original,
                (
                    ConflictResolution(
                        action_id=conflict.action_id,
                        choice=ConflictChoice.SEMANTIC_MERGE,
                        rendered_path=str(outside),
                        rendered_sha256=hashlib.sha256(b"unsafe\n").hexdigest(),
                    ),
                ),
                artifacts,
            )


if __name__ == "__main__":
    unittest.main()
