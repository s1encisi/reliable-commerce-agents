# 工作目录、分支与版本边界

## 当前布局

本轮升级使用独立开发分支 `feat/after-sales-reliability`；`main` 保留原快照，不合并升级内容。所有运行命令从仓库根目录执行，不依赖某台电脑的绝对路径。

当前版本是经过保密检查的独立快照。原始本地提交历史已保存在本机 Git bundle 备份中，不属于本仓库上传的提交历史。上游源码基线为 26f47c494dd6b371312593e82f066713f6f56e9c，来源为 [原始项目](https://github.com/nitin27may/e-commerce-agents)。

## 检查工作位置

    git rev-parse --show-toplevel
    git branch --show-current
    git status --short
    git worktree list

本轮预期分支为 `feat/after-sales-reliability`。查看本次修改：

    git diff --stat
    git diff --check

新增文件尚未暂存时不会出现在普通 git diff 中，要结合 git status 检查。

## 本地记录和上传边界

个人计划、工具设置、运行记录和完整历史备份保留在本机。已整理的技术方案见 [售后可靠性方案](after-sales-reliability-plan.md)。

Excel、数据导出、密钥、真实环境配置、数据库文件及私人工作目录不上传。不要用 git add -f 绕过排除规则。详细范围与本地推送检查见 [上传边界](../local-privacy.md)。

## 避免实验互相影响

- 基线与改进版使用不同的数据库快照和实验目录，记录各自的提交号。
- 完整 Compose 文件可能固定项目名、容器名、卷名或端口；仅更换文件夹不能保证运行隔离。
- 首轮在一套专用本地测试环境中串行切换基线与改进版，按同一脚本重置测试数据。
- 不通过预构建镜像证明本地源码改动有效。
- 不复制或打印真实用户数据、API 密钥和登录令牌来制作学习案例。
