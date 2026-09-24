# 发布流程

一个版本如何产出，以及镜像送到任何人手上之前必须满足什么条件。

## 各触发条件下会发生什么

| 触发条件 | 测试 | 镜像 | 发布为 |
|---|---|---|---|
| Pull request | 完整套件，外加评测与教程 | 构建、加载、冒烟测试，然后**丢弃** | 不发布 |
| 推送到 `main` | 完整套件 | 在全部测试任务通过**之后**构建并推送 | `:main`、`:sha-<7>` |
| 打标签 `vX.Y.Z` | 完整套件，针对该标签提交重跑 | 在测试通过**并经人工批准**之后构建并推送 | `:vX.Y.Z`、`:latest` |
| 打标签 `vX.Y.Z-rc.N` | 同上 | 同上 | 仅 `:vX.Y.Z-rc.N` —— `:latest` 不动 |

`.github/workflows/build-images.yml` 自身没有 `push` 或标签触发条件。它暴露 `workflow_call`，
发布权限由调用方授予 —— `main` 由 `tests.yml` 调用，标签由 `release.yml` 调用。
这才让门禁变得真实：在此之前的版本里，一个 semver 标签会在不依赖任何测试任务的情况下发布镜像，
于是给一个红灯提交打标签也会发版。

## 镜像标签

| 标签 | 含义 | 适用场景 |
|---|---|---|
| `:sha-<7>` | `main` 上每个提交对应一个不可变镜像 | 调试、回滚 |
| `:main` | `main` 的滚动最新提交，测试为绿 | 试用最新工作 |
| `:vX.Y.Z` | 不可变的正式版本 | 固定版本的部署 |
| `:latest` | 最新的**正式版本** | `docker-compose.demo.yml` |

`:latest` 有意跟踪最新正式版本，而不是 `main` 的最新提交。第一次来访、运行演示 compose 文件的人，
应当落到一个通过了完整门禁与人工检查的版本上，而不是一小时前刚合并的东西。

## 发布一个版本

1. **确认 `main` 是绿的。** 检查你要打标签的那个提交上的 `tests.yml`、`evals.yml` 与 `tutorials.yml`。

2. **在所有位置设置版本号。**

   ```bash
   python scripts/bump_version.py 1.2.0
   ```

   这会更新 `agents/python/pyproject.toml` 与 `web/package.json`，
   并打开一个空的 `CHANGELOG.md` 小节。

3. **撰写变更日志条目。** 填写脚本打开的那个小节。删掉一切非用户可见的内容 ——
   没有行为变化的重构不该出现在这里。写法与既有条目保持一致：说明什么东西坏了、坏了多久，
   而不是写「提升了可靠性」。

4. **提交并推送。**

   ```bash
   git commit -am "chore: release 1.2.0"
   git push
   ```

   等待 `main` 的 CI 完成。它会发布 `:main` 镜像，这是预期行为。

5. **打标签。**

   ```bash
   git tag v1.2.0 && git push origin v1.2.0
   ```

6. **批准。** `release.yml` 会针对该标签重跑完整套件，检查标签与
   `pyproject.toml` 是否一致、`CHANGELOG.md` 是否有对应小节，然后等待 `release`
   环境。查看结果并批准。

7. **验证镜像可以匿名拉取。**

   ```bash
   docker logout ghcr.io
   docker pull ghcr.io/s1encisi/reliable-commerce-agents/orchestrator:latest
   docker manifest inspect ghcr.io/s1encisi/reliable-commerce-agents/orchestrator:latest \
     | grep architecture
   ```

   应同时看到 `amd64` 与 `arm64`。这里失败几乎总是意味着某个新包被创建成了私有 —— 见下文。

8. **更新文档站点。** 首页的「项目当前状态」小节会写明当前版本号。

## 一次性设置

以下每一项如果被跳过，都会**静默**失败 —— 不会报错，只是安静地发生了错误的事。

### `release` 环境

Settings → Environments → New environment → `release`，并把 **Required reviewers** 设为你自己。

没有它，`release.yml` 中的 `environment: release` 就是空操作。审批任务会立即成功，
发布在无人参与的情况下完成，而日志里没有任何迹象表明这里本应有一道门禁。

### 包可见性 —— 无需操作

由**公开**仓库中的工作流发布的包会继承该仓库的可见性，因此它们创建时即为公开，
可直接匿名拉取。无需任何点击。

这一点是验证过的，而不是假设的：`auth-server`、`mcp-product` 与 `mcp-inventory`
在十镜像矩阵落地之前从未被 CI 构建过，而它们首次发布时，先执行 `docker logout ghcr.io`
（不保留任何已存凭据）再做匿名 `docker manifest inspect`，三者全部成功。

你在别处会看到的说法 —— GHCR 包初始为私有，需要逐个手动切换 ——
适用于从**私有**仓库发布、或使用个人访问令牌发布的包。这里不适用。

不过在任何一个*新*镜像首次发布后仍然值得检查，因为这种失败很安静 ——
私有包会以认证错误的形式失败，读起来像是网络问题。按访客的方式检查：

```bash
docker logout ghcr.io
for i in orchestrator product-discovery order-management pricing-promotions \
         review-sentiment inventory-fulfillment auth-server mcp-product \
         mcp-inventory frontend; do
  docker manifest inspect ghcr.io/s1encisi/reliable-commerce-agents/$i:latest >/dev/null 2>&1 \
    && echo "  public   $i" || echo "  PRIVATE  $i"
done
```

### 保留策略 —— 已自动化

GHCR 没有内置的保留设置，因此 `.github/workflows/package-cleanup.yml` 就是策略本身。
它每周对全部十个包运行一次，保留最近 20 个版本，且永不触碰 `:latest`、`:main`
或任何 `:vX.Y.Z` 标签。

否则会有两类东西无上限堆积：`main` 上每个提交对应一个 `:sha-<7>` 标签，
以及未打标签的版本 —— 多架构构建会推送一个 manifest list 外加每个平台一个镜像 manifest，
而标签移动后这些平台 manifest 就成了孤儿。后者在体量上是更大的来源，
除非你特意去找，否则在包的界面里是看不见的。

在信任它之前先手动跑一次。手动运行的默认行为是演练（dry run）：

```bash
gh workflow run package-cleanup.yml -f dry_run=true
gh run watch
```

## 版本管理

采用语义化版本。**git 标签是唯一事实来源**；版本文件由 `scripts/bump_version.py`
同步到它，若二者发生偏移，`release.yml` 的 `version-check` 任务会让发布失败。

该检查之所以存在，是因为它们确实曾经偏移过：`pyproject.toml` 写的是 `0.1.0`，
唯一的标签是 `v1.0.0`，而 README 写的是 v1.1 —— 一个已发布却从未打标签的版本，
因此也从未作为任何可拉取的产物存在过。

CI 会运行同样的检查：

```bash
python scripts/bump_version.py 1.2.0 --check
```

## 发布出错时

**标签打错了，还没发布任何东西** —— 删掉它重新来。

```bash
git tag -d v1.2.0 && git push --delete origin v1.2.0
```

**标签打错了，镜像已发布** —— 不要删除或覆盖已发布的标签。请发布 `v1.2.1`。
任何拉取过 `v1.2.0` 的人手里已经有了它；悄悄改变该标签指向的内容，比一次糟糕的发布更糟。

**`:latest` 指向了一个糟糕的版本** —— 把修复作为新版本发布。`:latest` 会随下一次成功发布前移，
永远不会被手动回退。

## 相关内容

- [配置](configuration.md) —— 各项设置从何而来
- [部署](deployment.md) —— 运行服务栈
- [`.claude/plans/remaining-work.md`](https://github.com/s1encisi/reliable-commerce-agents/blob/26f47c494dd6b371312593e82f066713f6f56e9c/.claude/plans/remaining-work.md) —— 汇总计划；产出这条流水线的构建与发布设计随 v1.2.0 交付，保存在 git 历史中
