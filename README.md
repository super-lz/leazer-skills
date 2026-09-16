# leazer-skills

个人 Skill 源码与第三方来源清单仓库。自有 Skill 以本仓库为长期维护的事实源，`~/.agents/skills` 仅保存运行时安装副本；第三方 Skill 只记录官方来源与核验版本，不复制上游内容。

## 结构

```text
skills/                     # 自有或正式 fork 后由本仓库维护的 Skill
registry/external.yaml      # 第三方来源、Skill 清单与核验快照
```

第三方的 `status: tracked` 只表示来源已登记；`source_verified: true` 表示来源已由官方文档核对，不等于内容已经完成安全或行为评测。

## Skills

- `ui-clarity-audit`：从第一性原理审查产品界面的冗余、自述和歧义，同时保护必要状态、安全与恢复信息。
- `flutter-brand-lifecycle`：指导 Flutter 多品牌新增、编辑、停用、删除和完整性审计，动态发现项目能力并按证据判断交付层级。

## 验证

需要当前 Python 环境已安装 PyYAML。

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" skills/ui-clarity-audit
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" skills/flutter-brand-lifecycle
```

第三方清单还应能被 YAML 解析，并保证 `id`、Skill 名称和来源不重复。更新 `resolved_commit` 前，先查看上游变更，再决定是否接受新版本。

## 第三方 Skill

当前登记了 Flutter 官方文档推荐的两组来源：

- `flutter/agent-plugins`：仅登记 10 个 `flutter-*` Skill。
- `dart-lang/skills`：登记 15 个 `dart-*` Skill。

完整清单与本次核验 commit 见 [`registry/external.yaml`](registry/external.yaml)。仓库不保存第三方 Skill 正文；使用时在目标 Flutter/Dart 项目根目录运行 `npx skills`。

先查看可安装内容：

```bash
npx skills add flutter/agent-plugins --list
npx skills add dart-lang/skills --list
```

安装单个 Skill 到目标项目的 `.agents/skills/`：

```bash
npx skills add flutter/agent-plugins \
  --skill flutter-add-widget-test \
  --agent universal
```

需要按清单中的核验版本复现时，使用固定 commit：

```bash
npx skills add \
  https://github.com/flutter/agent-plugins/tree/8c3fcb28036ac0f80713e372582ab0b1a1be59d8 \
  --skill flutter-add-widget-test \
  --agent universal
```

不要在本仓库根目录执行项目级安装，否则会把第三方运行副本写入本仓库的 `.agents/skills/`。也不要直接使用 `npx skills update` 代替审核；它会更新已安装内容，但不会替你检查上游行为变化。

## 本地安装

安装前先确认 `~/.agents/skills` 与 `~/.codex/skills` 中不存在同名 Skill。一个 Skill 只保留一个有效安装位置。

```bash
cp -R skills/ui-clarity-audit "$HOME/.agents/skills/"
cp -R skills/flutter-brand-lifecycle "$HOME/.agents/skills/"
```

所有修改先进入本仓库并完成验证，再更新安装副本。提交前不得包含凭据、个人原始资料或项目私密内容。
