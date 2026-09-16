from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "manage_norn_governance.py"
LEGACY_TEMPLATE = SKILL_ROOT / "assets" / "legacy-templates" / "0"
EXPECTED_FILES = {
    "AGENTS.md",
    "norn-governance/.norn.json",
    "norn-governance/AGENTS.md",
    "norn-governance/spec/AGENTS.md",
    "norn-governance/spec/main-spec.md",
    "norn-governance/appendix/README.md",
}


class NornGovernanceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        self.counter = 0

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_target(self) -> Path:
        self.counter += 1
        target = self.workspace / f"target-{self.counter}"
        target.mkdir()
        return target

    def copy_legacy_template(self) -> Path:
        target = self.make_target()
        shutil.copytree(LEGACY_TEMPLATE, target, dirs_exist_ok=True)
        return target

    def run_cli_raw(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(value) for value in arguments)],
            check=False,
            capture_output=True,
            text=True,
        )

    def run_cli_json(self, *arguments: object) -> dict:
        completed = self.run_cli_raw(*arguments, "--report-json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def analyze(self, target: Path, artifacts: Path | None = None) -> dict:
        artifacts = artifacts or self.workspace / f"artifacts-{self.counter}"
        return self.run_cli_json(
            "analyze",
            "--target",
            target,
            "--artifact-dir",
            artifacts,
        )

    def test_analyze_empty_project_writes_transaction_but_not_project_files(self) -> None:
        target = self.make_target()
        before = tuple(target.rglob("*"))

        report = self.analyze(target)

        self.assertEqual(report["command"], "analyze")
        self.assertEqual(report["structure_state"], "uninitialized")
        self.assertTrue(report["executable"])
        self.assertFalse(report["semantic_review_required"])
        self.assertTrue(Path(report["transaction_path"]).is_file())
        self.assertIn(
            "任意文档的内容职责正确性",
            report["verification_scope"]["excludes"],
        )
        self.assertEqual(report["actions"][0]["kind"], "create")
        self.assertTrue(all("受管" in action["reason"] or "Norn" in action["reason"] for action in report["actions"]))
        self.assertEqual(tuple(target.rglob("*")), before)
        self.assertEqual(
            {action["target_path"] for action in report["actions"]},
            EXPECTED_FILES,
        )

    def test_analyze_legacy_project_reports_relocations_without_writes(self) -> None:
        target = self.copy_legacy_template()
        before = {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }

        report = self.analyze(target)

        self.assertEqual(report["structure_state"], "legacy")
        self.assertTrue(report["semantic_review_required"])
        self.assertEqual(
            {
                action["source_path"]
                for action in report["actions"]
                if action["source_path"]
            },
            {
                "docs/AGENTS.md",
                "docs/spec/AGENTS.md",
                "docs/spec/main-spec.md",
                "docs/appendix/README.md",
            },
        )
        self.assertTrue(report["sections"]["relocations"])
        after = {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_default_scope_leaves_extra_legacy_content_out_of_transaction(self) -> None:
        """防止普通迁移在未获授权时扩大到项目自有文档。"""
        target = self.copy_legacy_template()
        extra = target / "docs/appendix/architecture.md"
        extra.write_text("project appendix\n", encoding="utf-8")

        report = self.analyze(target)

        touched_paths = {
            path
            for action in report["actions"]
            for path in (action["source_path"], action["target_path"])
            if path
        }
        self.assertNotIn("docs/appendix/architecture.md", touched_paths)
        self.assertNotIn("norn-governance/appendix/architecture.md", touched_paths)

    def test_explicit_all_scope_migrates_every_governance_tree_file(self) -> None:
        """防止用户明确要求完整迁移后仍只处理四个核心文件。"""
        target = self.copy_legacy_template()
        appendix_body = b"\x89PNG\r\nproject diagram\x00"
        appendix = target / "docs/appendix/diagram.png"
        appendix.write_bytes(appendix_body)
        specification = target / "docs/spec/secondary.md"
        specification.write_text("# Secondary contract\n", encoding="utf-8")
        unrelated = target / "docs/architecture.md"
        unrelated.write_text("outside governance trees\n", encoding="utf-8")
        artifacts = self.workspace / "all-tree-artifacts"

        report = self.run_cli_json(
            "analyze",
            "--target",
            target,
            "--artifact-dir",
            artifacts,
            "--include-legacy-tree",
            "all",
        )

        self.assertEqual(report["legacy_content_scopes"], ["appendix", "spec"])
        self.assertTrue(report["semantic_review_required"])
        relocations = {
            (action["source_path"], action["target_path"], action["kind"])
            for action in report["actions"]
            if action["source_path"]
        }
        self.assertIn(
            (
                "docs/appendix/diagram.png",
                "norn-governance/appendix/diagram.png",
                "move",
            ),
            relocations,
        )
        self.assertIn(
            (
                "docs/spec/secondary.md",
                "norn-governance/spec/secondary.md",
                "move",
            ),
            relocations,
        )
        self.assertNotIn(
            "docs/architecture.md",
            {
                path
                for action in report["actions"]
                for path in (action["source_path"], action["target_path"])
                if path
            },
        )

        applied = self.run_cli_json(
            "apply",
            "--target",
            target,
            "--transaction",
            report["transaction_path"],
        )

        self.assertEqual(applied["verification"]["structure_state"], "current")
        self.assertEqual(
            (target / "norn-governance/appendix/diagram.png").read_bytes(),
            appendix_body,
        )
        self.assertEqual(
            (target / "norn-governance/spec/secondary.md").read_text(
                encoding="utf-8"
            ),
            "# Secondary contract\n",
        )
        self.assertFalse(appendix.exists())
        self.assertFalse(specification.exists())
        self.assertEqual(
            unrelated.read_text(encoding="utf-8"),
            "outside governance trees\n",
        )

    def test_apply_requires_transaction_artifact(self) -> None:
        target = self.make_target()

        completed = self.run_cli_raw("apply", "--target", target)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--transaction", completed.stderr)

    def test_analyze_then_apply_initializes_and_verifies_project(self) -> None:
        target = self.make_target()
        analysis = self.analyze(target)

        applied = self.run_cli_json(
            "apply",
            "--target",
            target,
            "--transaction",
            analysis["transaction_path"],
        )

        self.assertEqual(applied["command"], "apply")
        self.assertEqual(applied["verification"]["structure_state"], "current")
        self.assertTrue(applied["verification"]["manifest_valid"])
        self.assertTrue(applied["verification"]["single_spec_source"])
        self.assertIn(
            "主规格语义完整性",
            applied["verification"]["scope"]["excludes"],
        )
        self.assertEqual(set(applied["created"]), EXPECTED_FILES)
        self.assertFalse((target / "docs").exists())
        self.assertFalse((target / "norn-governance/plans").exists())
        root_agents = (target / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("首行以 `🚨` 开头", root_agents)
        self.assertIn("首行以 `⚠️` 开头", root_agents)
        self.assertEqual(root_agents.count("🔵"), 1)
        self.assertIn("不得输出 `🔵`", root_agents)
        self.assertIn(
            "主规格内容没有实际变化时，禁止声称已经同步",
            root_agents,
        )

    def test_apply_rejects_tampered_transaction_digest(self) -> None:
        target = self.make_target()
        analysis = self.analyze(target)
        transaction_path = Path(analysis["transaction_path"])
        payload = json.loads(transaction_path.read_text(encoding="utf-8"))
        payload["target_root"] = str(self.make_target())
        transaction_path.write_text(json.dumps(payload), encoding="utf-8")

        completed = self.run_cli_raw(
            "apply", "--target", target, "--transaction", transaction_path
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("治理事务摘要不匹配", completed.stderr)

    def test_apply_rejects_target_that_differs_from_transaction(self) -> None:
        target = self.make_target()
        other_target = self.make_target()
        analysis = self.analyze(target)

        completed = self.run_cli_raw(
            "apply",
            "--target",
            other_target,
            "--transaction",
            analysis["transaction_path"],
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("目标路径与治理事务不一致", completed.stderr)
        self.assertEqual(tuple(other_target.iterdir()), ())

    def test_human_analysis_contains_decision_sections(self) -> None:
        target = self.copy_legacy_template()
        completed = self.run_cli_raw(
            "analyze",
            "--target",
            target,
            "--artifact-dir",
            self.workspace / "human-artifacts",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for heading in (
            "状态",
            "归属证据",
            "路径迁移",
            "规则升级",
            "冲突",
            "删除",
            "风险",
            "验证",
            "机器验证边界",
        ):
            self.assertIn(heading, completed.stdout)
        self.assertIn("Norn Governance", completed.stdout)

    def test_help_and_human_readable_json_values_are_chinese_first(self) -> None:
        help_result = self.run_cli_raw("analyze", "--help")

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("只读分析项目结构", help_result.stdout)
        self.assertIn("--target", help_result.stdout)
        self.assertIn("--report-json", help_result.stdout)

        target = self.make_target()
        report = self.analyze(target)
        self.assertEqual(report["command"], "analyze")
        self.assertEqual(report["structure_state"], "uninitialized")
        self.assertEqual(report["actions"][0]["kind"], "create")
        self.assertIn("受管", report["actions"][0]["evidence"][0])
        self.assertIn("主规格语义完整性", report["verification_scope"]["excludes"])

    def test_argument_errors_are_chinese_while_flags_remain_stable(self) -> None:
        missing_target = self.run_cli_raw("analyze")
        invalid_scope = self.run_cli_raw(
            "analyze",
            "--target",
            self.make_target(),
            "--include-legacy-tree",
            "unsupported",
        )

        self.assertEqual(missing_target.returncode, 2)
        self.assertIn("缺少必需参数", missing_target.stderr)
        self.assertIn("--target", missing_target.stderr)
        self.assertEqual(invalid_scope.returncode, 2)
        self.assertIn("无效选择", invalid_scope.stderr)
        self.assertIn("--include-legacy-tree", invalid_scope.stderr)

    def test_apply_report_does_not_claim_spec_sync_without_a_spec_writeback(self) -> None:
        target = self.make_target()
        analysis = self.analyze(target)

        completed = self.run_cli_raw(
            "apply",
            "--target",
            target,
            "--transaction",
            analysis["transaction_path"],
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("已同步主规格", completed.stdout)
        self.assertNotIn("已回写", completed.stdout)

    def test_resolve_adopt_template_rebuilds_executable_transaction(self) -> None:
        target = self.copy_legacy_template()
        with (target / "docs/AGENTS.md").open("a", encoding="utf-8") as stream:
            stream.write("\n## Project-specific docs rule\n")
        analysis = self.analyze(target)
        conflict = next(
            action
            for action in analysis["actions"]
            if action["target_path"] == "norn-governance/AGENTS.md"
        )
        resolutions_path = Path(analysis["transaction_path"]).parent / "resolutions.json"
        resolutions_path.write_text(
            json.dumps(
                {
                    "resolutions": [
                        {
                            "action_id": conflict["action_id"],
                            "choice": "adopt-template",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        resolved = self.run_cli_json(
            "resolve",
            "--target",
            target,
            "--transaction",
            analysis["transaction_path"],
            "--resolutions",
            resolutions_path,
        )

        self.assertTrue(resolved["executable"])
        self.assertFalse(resolved["sections"]["conflicts"])
        applied = self.run_cli_json(
            "apply",
            "--target",
            target,
            "--transaction",
            resolved["transaction_path"],
        )
        self.assertEqual(applied["verification"]["structure_state"], "current")


if __name__ == "__main__":
    unittest.main()
