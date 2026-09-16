from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from norn_governance.managed_markdown import (  # noqa: E402
    ManagedBlockError,
    parse_managed_blocks,
    replace_managed_block,
)
from norn_governance.models import NornManifest, OwnershipKind  # noqa: E402
from norn_governance.templates import (  # noqa: E402
    MANAGED_PATHS,
    TEMPLATE_VERSION,
    template_manifest,
)


class ManagedMarkdownTests(unittest.TestCase):
    def test_replace_managed_block_preserves_project_text(self) -> None:
        current = (
            "before\n"
            "<!-- norn:managed:start core-governance -->\nold\n"
            "<!-- norn:managed:end core-governance -->\n"
            "after\n"
        )
        replacement = (
            "<!-- norn:managed:start core-governance -->\nnew\n"
            "<!-- norn:managed:end core-governance -->"
        )

        self.assertEqual(
            replace_managed_block(current, "core-governance", replacement),
            "before\n" + replacement + "\nafter\n",
        )

    def test_parser_returns_exact_block_and_utf8_offsets(self) -> None:
        prefix = "项目规则\n"
        block = (
            "<!-- norn:managed:start core-governance -->\n治理内容\n"
            "<!-- norn:managed:end core-governance -->"
        )
        text = prefix + block + "\n项目扩展\n"

        parsed = parse_managed_blocks(text)

        self.assertEqual(tuple(parsed), ("core-governance",))
        self.assertEqual(parsed["core-governance"].start, len(prefix))
        self.assertEqual(parsed["core-governance"].end, len(prefix + block))
        self.assertEqual(parsed["core-governance"].text, block)
        self.assertEqual(len(parsed["core-governance"].sha256), 64)

    def test_duplicate_managed_block_is_rejected(self) -> None:
        duplicated = (
            "<!-- norn:managed:start core-governance -->\nx\n"
            "<!-- norn:managed:end core-governance -->\n"
            "<!-- norn:managed:start core-governance -->\ny\n"
            "<!-- norn:managed:end core-governance -->"
        )

        with self.assertRaisesRegex(ManagedBlockError, "重复"):
            parse_managed_blocks(duplicated)

    def test_missing_end_marker_is_rejected(self) -> None:
        text = "<!-- norn:managed:start core-governance -->\ncontent\n"

        with self.assertRaisesRegex(ManagedBlockError, "缺少结束标记"):
            parse_managed_blocks(text)

    def test_nested_managed_blocks_are_rejected(self) -> None:
        nested = (
            "<!-- norn:managed:start core-governance -->\n"
            "<!-- norn:managed:start governance-directory -->\n"
            "<!-- norn:managed:end governance-directory -->\n"
            "<!-- norn:managed:end core-governance -->"
        )

        with self.assertRaisesRegex(ManagedBlockError, "嵌套"):
            parse_managed_blocks(nested)

    def test_unknown_block_id_is_rejected(self) -> None:
        text = (
            "<!-- norn:managed:start unknown-governance -->\ncontent\n"
            "<!-- norn:managed:end unknown-governance -->"
        )

        with self.assertRaisesRegex(ManagedBlockError, "未知受管区块"):
            parse_managed_blocks(text)

    def test_replacement_must_be_exact_matching_block(self) -> None:
        current = (
            "<!-- norn:managed:start core-governance -->\nold\n"
            "<!-- norn:managed:end core-governance -->"
        )
        wrong = (
            "<!-- norn:managed:start governance-directory -->\nnew\n"
            "<!-- norn:managed:end governance-directory -->"
        )

        with self.assertRaisesRegex(ManagedBlockError, "替换内容"):
            replace_managed_block(current, "core-governance", wrong)


class TemplateManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asset_root = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "ai-project-governance-template"
        )
        repository_template = Path(__file__).resolve().parents[3] / "template"
        self.repository_template = (
            repository_template if repository_template.is_dir() else None
        )
        self.template_root = self.repository_template or self.asset_root

    def test_template_manifest_records_real_managed_block_hashes(self) -> None:
        manifest = template_manifest(self.template_root)

        self.assertEqual(manifest.template_version, TEMPLATE_VERSION)
        self.assertEqual(tuple(manifest.managed_files), tuple(sorted(MANAGED_PATHS)))
        for relative_path, (ownership, block_ids) in MANAGED_PATHS.items():
            with self.subTest(path=relative_path):
                record = manifest.managed_files[relative_path]
                self.assertEqual(record.ownership, ownership)
                self.assertEqual(record.managed_blocks, block_ids)
                text = (self.template_root / relative_path).read_text(encoding="utf-8")
                blocks = parse_managed_blocks(text)
                if ownership is OwnershipKind.PROJECT:
                    self.assertEqual(blocks, {})
                    self.assertIsNone(record.base_sha256)
                else:
                    self.assertEqual(tuple(blocks), block_ids)
                    self.assertEqual(record.base_sha256, blocks[block_ids[0]].sha256)

    def test_current_template_has_non_noisy_specification_notice_policy(self) -> None:
        root_agents = (self.template_root / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("首行以 `🚨` 开头", root_agents)
        self.assertIn("首行以 `⚠️` 开头", root_agents)
        self.assertIn(
            "主规格内容没有实际变化时，禁止声称已经同步",
            root_agents,
        )
        self.assertIn("不得输出 `🔵`", root_agents)
        self.assertEqual(root_agents.count("🔵"), 1)

    def test_current_template_developer_documents_are_chinese_first(self) -> None:
        expected_phrases = {
            "AGENTS.md": "Norn 协作约定",
            "norn-governance/AGENTS.md": "Norn 治理目录",
            "norn-governance/spec/AGENTS.md": "主规格治理",
            "norn-governance/spec/main-spec.md": "主实现规格",
            "norn-governance/appendix/README.md": "附录",
        }

        for relative_path, phrase in expected_phrases.items():
            with self.subTest(path=relative_path):
                text = (self.template_root / relative_path).read_text(encoding="utf-8")
                self.assertIn(phrase, text)

    def test_template_version_is_v5(self) -> None:
        self.assertEqual(TEMPLATE_VERSION, 5)

    def test_static_manifest_matches_computed_template_manifest(self) -> None:
        computed = template_manifest(self.template_root)
        stored = NornManifest.from_dict(
            json.loads(
                (self.template_root / "norn-governance/.norn.json").read_text(
                    encoding="utf-8"
                )
            )
        )

        self.assertEqual(stored.to_dict(), computed.to_dict())

    def test_repository_template_and_skill_asset_are_byte_identical(self) -> None:
        if self.repository_template is None:
            self.skipTest("canonical repository template is not installed with the Skill")
        relative_paths = (*MANAGED_PATHS, "norn-governance/.norn.json")

        for relative_path in relative_paths:
            with self.subTest(path=relative_path):
                self.assertEqual(
                    (self.asset_root / relative_path).read_bytes(),
                    (self.repository_template / relative_path).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
