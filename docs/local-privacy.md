# 本地数据与上传边界

本仓库保存源码、经过检查的技术文档、配置模板和合成测试样例。仓库私密性不能替代文件排除和内容检查。

## 默认保留在本机

- Excel、CSV/TSV、数据集导出、模型与分析产物、数据库文件及备份。
- 真实 .env、密钥、证书、认证状态、凭据文件。
- .claude/、.codex/、.agents/ 中的个人计划、记忆、工具配置和运行记录。
- 本地业务或研究用 Office/PDF 文件、压缩包和完整 Git 历史备份。
- 根目录的 data、datasets、exports、reports、results、private、confidential、secrets 和 local-only。

具体匹配规则见根目录 .gitignore。只有经过检查的 .env.example、.env.minimal 等模板可以上传；不要向模板填写真实凭据。

项目原有的合成 JSON 测试样例和数据库建表 SQL 继续保留。新增真实业务数据不能因为扩展名相同就被视为可上传。

## 提交与推送

提交前检查 git status --short 和 git diff --cached。不使用 git add -f 绕过数据排除规则，不推送历史备份、旧分支或所有标签。

完成本次连接的本机已配置 pre-push 检查：只允许向配置的私密 origin 推送 main，检查提交历史中的禁止路径，并运行本地 Gitleaks。该 hook 与扫描器保存在 .git/，不会自动安装到其他克隆。

本次远端仓库关闭 GitHub Actions，避免导入上游工作流时自动发布文档、镜像或执行其他外部动作；启用前应检查工作流的权限、目标和费用。

## 历史与恢复

远端使用经过检查的当前快照作为初始提交，不包含原本可能携带私人记录的提交历史。原始完整历史和配置保留在本机 .git/private-upload-audit/；不要上传这个目录或其中的 bundle 文件。

忽略规则和密钥扫描无法自动识别所有商业秘密。上传新的业务数据、客户材料或研究原始数据前，仍应检查其来源和内容。
