# leazer-skills

个人 Codex Skill 源码仓库。仓库内容是长期维护的事实源，`~/.agents/skills` 仅保存运行时安装副本。

## Skills

- `ui-clarity-audit`：从第一性原理审查产品界面的冗余、自述和歧义，同时保护必要状态、安全与恢复信息。
- `flutter-brand-lifecycle`：指导 Flutter 多品牌新增、编辑、停用、删除和完整性审计，动态发现项目能力并按证据判断交付层级。

## 验证

需要当前 Python 环境已安装 PyYAML。

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" skills/ui-clarity-audit
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" skills/flutter-brand-lifecycle
```

## 本地安装

安装前先确认 `~/.agents/skills` 与 `~/.codex/skills` 中不存在同名 Skill。一个 Skill 只保留一个有效安装位置。

```bash
cp -R skills/ui-clarity-audit "$HOME/.agents/skills/"
cp -R skills/flutter-brand-lifecycle "$HOME/.agents/skills/"
```

所有修改先进入本仓库并完成验证，再更新安装副本。提交前不得包含凭据、个人原始资料或项目私密内容。
