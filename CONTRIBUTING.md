# 贡献指南

欢迎通过 Pull Request 改进现有 Skill 或提交新的 Skill。

1. Fork 仓库并创建修改分支。
2. 完成修改和相关验证。
3. 提交 Pull Request，说明改了什么、为什么修改以及执行了哪些验证。

请确保：

- Skill 包含有效的 `SKILL.md`、`name` 和 `description`。
- 内容不包含凭据、项目私密信息或个人原始资料。
- 修改脚本时运行相关测试；修改 Norn 时运行：

```bash
python3 -m unittest discover -s skills/norn-governance/tests -q
```
