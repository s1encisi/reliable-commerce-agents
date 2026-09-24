## 改了什么

<!-- 它做了什么，以及为什么。如果修复了某个 issue，写 "Fixes #N"。 -->

## 如何验证

<!-- 你运行了哪些命令、它们输出了什么。单说「测试通过」不算验证 ——
     本仓库有据可查的历史里，出现过通过了所有测试、实跑却完全坏掉的改动。 -->

```
```

## 检查清单

- [ ] 从 `main` 拉分支，且只聚焦一件事
- [ ] 新增或调整了测试 —— 每一处改动都要随附测试
- [ ] 行为变更时更新了文档（`docs/` 是站点的来源）
- [ ] 本地通过了「完成定义」：

```bash
cd agents/python && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build
```

## 影响的层次

- [ ] Python 后端
- [ ] 前端
- [ ] 仅文档 / 教程

## 是否对着运行中的环境验证过？

- [ ] 是 —— 方式：<!-- 例如 ./scripts/dev.sh --demo，然后问「...」 -->
- [ ] 否 —— 仅做了可单测的改动
