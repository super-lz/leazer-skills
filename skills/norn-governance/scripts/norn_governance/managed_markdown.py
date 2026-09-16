from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


START_PREFIX = "<!-- norn:managed:start "
END_PREFIX = "<!-- norn:managed:end "
KNOWN_BLOCK_IDS = frozenset(
    {
        "core-governance",
        "governance-directory",
        "specification-governance",
        "appendix-governance",
    }
)
_MARKER_PATTERN = re.compile(
    r"^<!-- norn:managed:(start|end) ([a-z0-9][a-z0-9-]*) -->$",
    re.MULTILINE,
)


class ManagedBlockError(ValueError):
    """Norn 受管 Markdown 边界不安全时抛出。"""


@dataclass(frozen=True)
class ManagedBlock:
    block_id: str
    start: int
    end: int
    text: str
    sha256: str


def parse_managed_blocks(text: str) -> Mapping[str, ManagedBlock]:
    if not isinstance(text, str):
        raise ManagedBlockError("受管 Markdown 必须是文本")

    blocks: dict[str, ManagedBlock] = {}
    active_id: str | None = None
    active_start: int | None = None

    for marker in _MARKER_PATTERN.finditer(text):
        marker_kind, block_id = marker.groups()
        if block_id not in KNOWN_BLOCK_IDS:
            raise ManagedBlockError(f"未知受管区块：{block_id}")

        if marker_kind == "start":
            if active_id is not None:
                raise ManagedBlockError(
                    f"受管区块 {block_id} 嵌套在 {active_id} 中"
                )
            if block_id in blocks:
                raise ManagedBlockError(f"受管区块重复：{block_id}")
            active_id = block_id
            active_start = marker.start()
            continue

        if active_id is None:
            raise ManagedBlockError(f"意外的结束标记：{block_id}")
        if block_id != active_id:
            raise ManagedBlockError(
                f"受管区块结束标记不匹配：期望 {active_id}，实际 {block_id}"
            )

        assert active_start is not None
        block_text = text[active_start : marker.end()]
        blocks[block_id] = ManagedBlock(
            block_id=block_id,
            start=active_start,
            end=marker.end(),
            text=block_text,
            sha256=hashlib.sha256(block_text.encode("utf-8")).hexdigest(),
        )
        active_id = None
        active_start = None

    if active_id is not None:
        raise ManagedBlockError(f"受管区块缺少结束标记：{active_id}")

    return MappingProxyType(blocks)


def replace_managed_block(text: str, block_id: str, replacement: str) -> str:
    current_blocks = parse_managed_blocks(text)
    if block_id not in current_blocks:
        raise ManagedBlockError(f"找不到受管区块：{block_id}")

    replacement_blocks = parse_managed_blocks(replacement)
    if tuple(replacement_blocks) != (block_id,):
        raise ManagedBlockError(
            f"替换内容必须且只能包含受管区块：{block_id}"
        )
    replacement_block = replacement_blocks[block_id]
    if replacement_block.start != 0 or replacement_block.end != len(replacement):
        raise ManagedBlockError("替换内容不得包含受管区块外文本")

    current = current_blocks[block_id]
    return text[: current.start] + replacement + text[current.end :]
