# 工作流图

由 [`agents/python/config/workflows/`](../../agents/python/config/workflows/) 下的 YAML 规格自动生成。不要手工编辑本目录下的
文件——每次运行生成器时它们都会被覆盖。

## 重新生成

```bash
source agents/.venv/bin/activate
python scripts/visualize_workflows.py
```

该命令会提交刷新后的 `.mmd` 和 `.dot` 文件。GitHub 会在 PR 和 wiki 中内联渲染 Mermaid；
把 DOT 交给 Graphviz 处理，即可生成用于架构文档的 PNG/SVG。

## CI 漂移检查

当 `WORKFLOW_VISUALIZATION_ON_BUILD=true` 时，CI 会带上 `--check` 运行该脚本：

```bash
python scripts/visualize_workflows.py --check
```

只要有文件缺失、内容与当前规格不一致，或者是孤儿文件（已经没有任何规格会生成它），退出码就是
1。错误信息会打印出修复它所需运行的确切命令。

## 当前工作流

| 名称 | 说明 | 来源规格 |
|------|-------------|-------------|
| `text-pipeline` | 经典的三阶段示例：转大写 → 非空校验门 → 记录前缀 | [`text-pipeline.yaml`](../../agents/python/config/workflows/text-pipeline.yaml) |

生产级工作流（`return-replace`、`pre-purchase`）会随第 7 阶段的步骤 08 和 09 一起落地。
