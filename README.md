# leazer-skills

个人 Skill 源码仓库。

## Skills

- `ui-clarity-audit`：从第一性原理审查产品界面的冗余、自述和歧义，同时保护必要状态、安全与恢复信息。
- `flutter-brand-lifecycle`：指导 Flutter 多品牌新增、编辑、停用、删除和完整性审计，动态发现项目能力并按证据判断交付层级。
- `norn-governance`：初始化、迁移、升级或审计项目中的 Norn 治理，维护主规格、活动计划和非权威附录。

## 安装

在目标项目根目录安装指定 Skill：

```bash
npx skills add super-lz/leazer-skills \
  --skill ui-clarity-audit \
  --agent universal
```

从本地源码安装：

```bash
cp -R skills/ui-clarity-audit "$HOME/.agents/skills/"
cp -R skills/flutter-brand-lifecycle "$HOME/.agents/skills/"
cp -R skills/norn-governance "$HOME/.agents/skills/"
```
