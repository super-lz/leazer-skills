#!/usr/bin/env python3
"""Norn Governance 的确定性分析、冲突解析和执行入口。"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from norn_governance.analyzer import (
    analyze_governance,
    normalize_legacy_content_scopes,
    resolve_conflicts,
)
from norn_governance.executor import ApplyResult, apply_transaction
from norn_governance.models import (
    ActionKind,
    ConflictResolution,
    GovernanceTransaction,
    load_transaction,
    write_transaction,
)


BRAND = "Norn Governance"


class ChineseArgumentParser(argparse.ArgumentParser):
    """保留 argparse 行为，只把内置帮助标签本地化。"""

    @staticmethod
    def _localize(text: str) -> str:
        return (
            text
            .replace("usage:", "用法:", 1)
            .replace("positional arguments:", "位置参数:")
            .replace("optional arguments:", "选项:")
            .replace("options:", "选项:")
            .replace("show this help message and exit", "显示帮助并退出")
            .replace("the following arguments are required:", "缺少必需参数：")
            .replace("unrecognized arguments:", "无法识别的参数：")
            .replace("invalid choice:", "无效选择：")
            .replace("choose from", "可选值")
            .replace("expected one argument", "需要一个值")
            .replace("argument ", "参数 ")
        )

    def format_usage(self) -> str:
        return self._localize(super().format_usage())

    def format_help(self) -> str:
        return self._localize(super().format_help())

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}：错误：{self._localize(message)}\n")


def _add_common_report_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report-json",
        action="store_true",
        help="输出机器可读 JSON 报告。",
    )


def parse_args() -> argparse.Namespace:
    parser = ChineseArgumentParser(
        description=f"{BRAND}：安全初始化、迁移或升级项目 AI 协作治理。"
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        title="命令",
        parser_class=ChineseArgumentParser,
    )

    analyze = commands.add_parser(
        "analyze",
        help="只读分析项目结构并生成带指纹的临时治理事务。",
        description="只读分析项目结构并生成带指纹的临时治理事务。",
    )
    analyze.add_argument("--target", required=True, help="目标仓库根目录。")
    analyze.add_argument(
        "--artifact-dir",
        help="治理事务和渲染产物目录；必须位于目标仓库之外。",
    )
    analyze.add_argument(
        "--include-legacy-tree",
        action="append",
        choices=("appendix", "spec", "all"),
        default=[],
        help="经用户明确授权后，递归迁移指定旧治理目录；可重复传入。",
    )
    _add_common_report_argument(analyze)

    resolve = commands.add_parser(
        "resolve",
        help="将明确的冲突选择绑定到已有治理事务。",
        description="将明确的冲突选择绑定到已有治理事务。",
    )
    resolve.add_argument("--target", required=True, help="目标仓库根目录。")
    resolve.add_argument(
        "--transaction", required=True, help="待解析的临时 transaction.json。"
    )
    resolve.add_argument(
        "--resolutions",
        required=True,
        help="包含 resolutions 数组的 JSON 文件。",
    )
    _add_common_report_argument(resolve)

    apply = commands.add_parser(
        "apply",
        help="重新验证治理事务、项目指纹和渲染产物后执行。",
        description="重新验证治理事务、项目指纹和渲染产物后执行。",
    )
    apply.add_argument("--target", required=True, help="目标仓库根目录。")
    apply.add_argument(
        "--transaction",
        required=True,
        help="已确认且无冲突的临时 transaction.json。",
    )
    _add_common_report_argument(apply)
    return parser.parse_args()


def _canonical_target(raw_target: str) -> Path:
    target = Path(raw_target).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"目标路径不存在：{target}")
    if not target.is_dir():
        raise ValueError(f"目标路径不是目录：{target}")
    return target


def _require_transaction_target(transaction: GovernanceTransaction, target: Path) -> None:
    if Path(transaction.target_root) != target:
        raise ValueError(
            "目标路径与治理事务不一致："
            f"target={target}，transaction={transaction.target_root}"
        )


def _transaction_sections(transaction: GovernanceTransaction) -> dict[str, list[dict[str, Any]]]:
    ownership_evidence: list[dict[str, Any]] = []
    relocations: list[dict[str, Any]] = []
    rule_upgrades: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    deletions: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []

    for action in transaction.actions:
        ownership_evidence.append(
            {
                "action_id": action.action_id,
                "target_path": action.target_path,
                "ownership": action.ownership.value,
                "evidence": list(action.evidence),
            }
        )
        if action.source_path is not None:
            relocations.append(
                {
                    "action_id": action.action_id,
                    "source_path": action.source_path,
                    "target_path": action.target_path,
                    "kind": action.kind.value,
                }
            )
            deletions.append(
                {
                    "action_id": action.action_id,
                    "path": action.source_path,
                    "condition": "canonical 目标已写入并验证",
                }
            )
        if action.kind in {ActionKind.CREATE, ActionKind.MERGE, ActionKind.MOVE}:
            rule_upgrades.append(
                {
                    "action_id": action.action_id,
                    "target_path": action.target_path,
                    "kind": action.kind.value,
                    "reason": action.reason,
                }
            )
        if action.kind is ActionKind.CONFLICT:
            conflicts.append(
                {
                    "action_id": action.action_id,
                    "target_path": action.target_path,
                    "reason": action.reason,
                    "allowed_resolutions": [
                        choice.value for choice in action.allowed_resolutions
                    ],
                }
            )
        if action.kind is ActionKind.DELETE:
            deletions.append(
                {
                    "action_id": action.action_id,
                    "path": action.target_path,
                    "condition": action.reason,
                }
            )
        if action.kind is not ActionKind.KEEP:
            risks.append(
                {
                    "action_id": action.action_id,
                    "target_path": action.target_path,
                    "risk": action.risk,
                }
            )
        verification.append(
            {
                "action_id": action.action_id,
                "target_path": action.target_path,
                "checks": list(action.verification),
            }
        )

    return {
        "ownership_evidence": ownership_evidence,
        "relocations": relocations,
        "rule_upgrades": rule_upgrades,
        "conflicts": conflicts,
        "deletions": deletions,
        "risks": risks,
        "verification": verification,
    }


def _transaction_report(
    command: str,
    transaction: GovernanceTransaction,
    transaction_path: Path,
    legacy_content_scopes: tuple[str, ...] = (),
) -> dict[str, Any]:
    action_counts = Counter(action.kind.value for action in transaction.actions)
    semantic_review_required = any(
        action.source_path is not None
        and (
            action.target_path == "norn-governance/spec/main-spec.md"
            or action.source_path
            not in {
                "docs/AGENTS.md",
                "docs/spec/AGENTS.md",
                "docs/appendix/README.md",
            }
        )
        for action in transaction.actions
    )
    return {
        "brand": BRAND,
        "command": command,
        "target": transaction.target_root,
        "structure_state": transaction.project_state.value,
        "template_version": transaction.template_version,
        "legacy_content_scopes": list(legacy_content_scopes),
        "transaction_path": str(transaction_path),
        "transaction_sha256": transaction.transaction_sha256,
        "semantic_review_required": semantic_review_required,
        "verification_scope": {
            "guarantees": [
                "受管结构和模板版本",
                "路径指纹和渲染产物哈希",
                "唯一 canonical 主规格路径",
            ],
            "excludes": [
                "主规格语义完整性",
                "任意文档的内容职责正确性",
                "规格与代码一致性",
                "产品实现正确性",
            ],
        },
        "executable": not transaction.conflicts
        and all(action.kind is not ActionKind.CONFLICT for action in transaction.actions),
        "summary": {
            "total_actions": len(transaction.actions),
            "action_counts": dict(sorted(action_counts.items())),
        },
        "actions": [action.to_dict() for action in transaction.actions],
        "sections": _transaction_sections(transaction),
    }


def _verification_dict(result: ApplyResult) -> dict[str, Any]:
    verification = result.verification
    return {
        "structure_state": verification.state.value,
        "manifest_valid": verification.manifest_valid,
        "single_spec_source": verification.single_spec_source,
        "checked_paths": list(verification.checked_paths),
        "warnings": list(verification.warnings),
        "scope": {
            "guarantees": [
                "受管结构和模板版本",
                "唯一 canonical 主规格路径",
            ],
            "excludes": [
                "主规格语义完整性",
                "任意文档的内容职责正确性",
                "规格与代码一致性",
                "产品实现正确性",
            ],
        },
    }


def _apply_report(target: Path, transaction_path: Path, result: ApplyResult) -> dict[str, Any]:
    return {
        "brand": BRAND,
        "command": "apply",
        "target": str(target),
        "transaction_path": str(transaction_path),
        "created": list(result.created),
        "updated": list(result.updated),
        "removed": list(result.removed),
        "removed_directories": list(result.removed_directories),
        "verification": _verification_dict(result),
    }


def _load_resolutions(path: Path) -> tuple[ConflictResolution, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"resolutions 文件无效：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("resolutions 文件根节点必须是对象")
    raw_resolutions = payload.get("resolutions")
    if not isinstance(raw_resolutions, list):
        raise ValueError("resolutions 必须是数组")
    resolutions: list[ConflictResolution] = []
    for index, raw_resolution in enumerate(raw_resolutions):
        if not isinstance(raw_resolution, Mapping):
            raise ValueError(f"resolutions[{index}] 必须是对象")
        resolutions.append(ConflictResolution.from_dict(raw_resolution))
    return tuple(resolutions)


def _print_items(items: list[dict[str, Any]], formatter) -> None:
    if not items:
        print("  - 无")
        return
    for item in items:
        print(f"  - {formatter(item)}")


def print_human_transaction_report(report: Mapping[str, Any]) -> None:
    sections = report["sections"]
    print(f"{BRAND} 分析报告")
    print(f"目标：{report['target']}")
    print(f"临时治理事务：{report['transaction_path']}")
    if report["legacy_content_scopes"]:
        print("显式旧目录范围：" + "、".join(report["legacy_content_scopes"]))
    print("\n状态")
    print(
        f"  - {report['structure_state']}；"
        f"{'可执行' if report['executable'] else '需要先解决冲突'}"
    )
    print(
        "  - 文档语义归位审计："
        + ("需要由 Skill 完成" if report["semantic_review_required"] else "当前事务未触发")
    )
    print("\n归属证据")
    _print_items(
        sections["ownership_evidence"],
        lambda item: (
            f"{item['target_path']} [{item['ownership']}]："
            + "；".join(item["evidence"])
        ),
    )
    print("\n路径迁移")
    _print_items(
        sections["relocations"],
        lambda item: f"{item['source_path']} -> {item['target_path']} ({item['kind']})",
    )
    print("\n规则升级")
    _print_items(
        sections["rule_upgrades"],
        lambda item: f"{item['target_path']} ({item['kind']})：{item['reason']}",
    )
    print("\n冲突")
    _print_items(
        sections["conflicts"],
        lambda item: (
            f"{item['target_path']}：{item['reason']}；"
            f"可选={','.join(item['allowed_resolutions']) or '需外部修正后重析'}"
        ),
    )
    print("\n删除")
    _print_items(
        sections["deletions"],
        lambda item: f"{item['path']}：{item['condition']}",
    )
    print("\n风险")
    _print_items(
        sections["risks"],
        lambda item: f"{item['target_path']}：{item['risk']}",
    )
    print("\n验证")
    _print_items(
        sections["verification"],
        lambda item: f"{item['target_path']}：{'；'.join(item['checks'])}",
    )
    print("\n机器验证边界")
    print("  - 保证：" + "；".join(report["verification_scope"]["guarantees"]))
    print("  - 不保证：" + "；".join(report["verification_scope"]["excludes"]))


def print_human_apply_report(report: Mapping[str, Any]) -> None:
    print(f"{BRAND} 执行报告")
    print(f"目标：{report['target']}")
    for heading, key in (
        ("已创建", "created"),
        ("已更新", "updated"),
        ("已删除文件", "removed"),
        ("已删除空目录", "removed_directories"),
    ):
        print(f"\n{heading}")
        values = report[key]
        if values:
            for value in values:
                print(f"  - {value}")
        else:
            print("  - 无")
    verification = report["verification"]
    print("\n验证")
    print(f"  - 结构状态：{verification['structure_state']}")
    print(f"  - manifest 有效：{verification['manifest_valid']}")
    print(f"  - 单一规格源：{verification['single_spec_source']}")
    for warning in verification["warnings"]:
        print(f"  - 警告：{warning}")
    print("  - 不保证：" + "；".join(verification["scope"]["excludes"]))


def _emit(report: Mapping[str, Any], report_json: bool) -> None:
    if report_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif report["command"] == "apply":
        print_human_apply_report(report)
    else:
        print_human_transaction_report(report)


def _run_analyze(args: argparse.Namespace, target: Path) -> dict[str, Any]:
    artifact_root = (
        Path(args.artifact_dir).expanduser().resolve()
        if args.artifact_dir
        else Path(tempfile.mkdtemp(prefix="norn-governance-"))
    )
    legacy_content_scopes = normalize_legacy_content_scopes(
        args.include_legacy_tree
    )
    transaction = analyze_governance(
        target,
        artifact_root,
        legacy_content_scopes=legacy_content_scopes,
    )
    return _transaction_report(
        "analyze",
        transaction,
        artifact_root / "transaction.json",
        legacy_content_scopes,
    )


def _run_resolve(args: argparse.Namespace, target: Path) -> dict[str, Any]:
    transaction_path = Path(args.transaction).expanduser().resolve()
    transaction = load_transaction(transaction_path)
    _require_transaction_target(transaction, target)
    resolutions = _load_resolutions(Path(args.resolutions).expanduser().resolve())
    resolved = resolve_conflicts(transaction, resolutions, transaction_path.parent)
    resolved_path = write_transaction(resolved, transaction_path.parent)
    return _transaction_report("resolve", resolved, resolved_path)


def _run_apply(args: argparse.Namespace, target: Path) -> dict[str, Any]:
    transaction_path = Path(args.transaction).expanduser().resolve()
    transaction = load_transaction(transaction_path)
    _require_transaction_target(transaction, target)
    result = apply_transaction(transaction_path)
    return _apply_report(target, transaction_path, result)


def main() -> int:
    args = parse_args()
    try:
        target = _canonical_target(args.target)
        if args.command == "analyze":
            report = _run_analyze(args, target)
        elif args.command == "resolve":
            report = _run_resolve(args, target)
        else:
            report = _run_apply(args, target)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"{BRAND} 失败：{exc}", file=sys.stderr)
        return 1
    _emit(report, args.report_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
