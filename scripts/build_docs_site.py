#!/usr/bin/env python3
"""从仓库 Markdown 构建文档站点。

仓库是内容来源，站点只是渲染结果。构建时向忽略的 _site_src 注入
Jekyll 元数据，不向原文添加前置元数据。教程、文档及源码间的相对
链接统一改写为站内路径或 GitHub 文件地址；--check 用于发现漂移。

使用方式：
    uv run python scripts/build_docs_site.py
    uv run python scripts/build_docs_site.py --check

构建命令写入生成目录，不执行远端发布。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "_site_src"
GITHUB_BASE = "https://github.com/s1encisi/reliable-commerce-agents"
GITHUB_BRANCH = "main"

# 与 docs/_config.yml 的 description 保持一致。
# 作为最后兜底描述，也用于检查页面
# 是否缺少自己的摘要。
SITE_DESCRIPTION = "可靠电商多智能体平台：基于微软智能体框架，包含核心概念、34 章教程与 Python 参考实现。"

# 章节分类
#
# 按读者的学习路径排序，而非磁盘目录，
# 先理解概念，再阅读架构，
# 矩阵和术语等查询资料放在最后，
# 便于按需查阅。


@dataclass(frozen=True)
class Section:
    """一个顶层导航章节。"""

    title: str
    nav_order: int
    summary: str
    # 仓库相对的 markdown 路径，按应出现的顺序排列。
    pages: tuple[str, ...] = ()
    # 当仓库里已经有一个值得发布的文件时，用它作为本章节的落地页。教程与
    # Concepts 都有 —— 它们的 README 携带学习路径表和两条阅读路线，而一句
    # 合成的摘要会把这些全部丢掉。这还能让 3 处指向 `../README.md` 的章节链接
    # 解析到站点自身的首页，而不是把读者弹到 GitHub 上。
    index_source: str | None = None


SECTIONS: tuple[Section, ...] = (
    Section(
        "Getting Started",
        2,
        "启动服务、了解部署方式，并排查常见问题。",
        (
            "docs/quick-start.md",
            "docs/configuration.md",
            "docs/demo-guide.md",
            "docs/deployment.md",
            "docs/troubleshooting.md",
            "docs/roadmap.md",
        ),
    ),
    Section(
        "Concepts",
        3,
        "从智能体基本概念出发，解释多智能体协作及项目术语。",
        (),  # 从下方 docs/concepts/ 扫描填充。
        index_source="docs/concepts/README.md",
    ),
    Section(
        "Tutorials",
        4,
        "34 章教程，从单个智能体逐步连接到完整项目，包含 Python 示例与配置指南。",
        (),  # 从下方 tutorials/ 扫描填充。
        index_source="tutorials/README.md",
    ),
    Section(
        "Architecture",
        5,
        "了解运行中的应用如何连接各模块。",
        (
            "docs/architecture.md",
            "docs/agent-flows.md",
            "docs/database-schema.md",
            "docs/api-reference.md",
            "docs/frontend.md",
            "docs/workflows/README.md",
        ),
    ),
    Section(
        "Guides",
        6,
        "面向具体任务的操作指引，用于扩展与运维这套系统。",
        (
            "docs/adding-an-agent.md",
            "docs/agent-upgrade.md",
            "docs/upgrade-verification.md",
            "docs/mcp-integration.md",
            "docs/telemetry.md",
            "docs/security-guide.md",
            "docs/agent-quality.md",
            "docs/maf-best-practices.md",
            "docs/releasing.md",
        ),
    ),
    Section(
        "Reference",
        7,
        "查询能力覆盖、智能体控制矩阵、术语表与绘图约定。",
        (
            "docs/parity-matrix.md",
            "docs/agent-audit-matrix.md",
            "docs/orchestration-benchmark.md",
            "docs/reported-vs-actual.md",
            "docs/adr/README.md",
            "docs/adr/0001-a2a-over-direct-calls.md",
            "docs/adr/0002-no-text-to-sql.md",
            "docs/adr/0003-yaml-prompt-composition.md",
            "docs/adr/0004-maf-native-execution.md",
            "docs/adr/0005-dual-stack-parity.md",
            "tutorials/_shared/jargon-glossary.md",
            "tutorials/_shared/mermaid-style-guide.md",
        ),
    ),
)

# 教程层级与 tutorials/README.md 一致，
# 不能在站点中另行编号，
# 否则同一组章节会得到不同层级名称，
# 使两个入口互相矛盾。
# 配置、完整项目导览和补充章节不属于数字层级，
# 保留独立分组。
TIERS: tuple[tuple[str, range], ...] = (
    ("Setup", range(0, 1)),
    ("Tier 1 — Core Agent", range(1, 5)),
    ("Tier 2 — Agent Internals", range(5, 9)),
    ("Tier 3 — Workflow Foundations", range(9, 12)),
    ("Tier 4 — Orchestrations", range(12, 17)),
    ("Tier 5 — Advanced", range(17, 21)),
    ("Capstone", range(21, 22)),
    ("Bonus Pattern", range(22, 23)),
    ("Tier 6 — Missing Concepts", range(23, 28)),
    ("Tier 7 — Patterns Without Production Wiring", range(28, 32)),
    ("Cost Control", range(32, 33)),
)


@dataclass
class Page:
    """由仓库源文档生成的一个站点页面。"""

    source: Path  # 相对仓库根目录。
    out_path: Path  # 相对 OUT_DIR。
    title: str
    nav_order: int
    parent: str | None = None
    grand_parent: str | None = None
    has_children: bool = False
    body: str = ""
    # 脚本合成的章节索引没有源文件，
    # 因此不生成原文回链。
    generated: bool = False


def chapter_tier(slug: str) -> str:
    """将章节目录名映射到所属层级标题。"""
    match = re.match(r"(\d+)", slug)
    number = int(match.group(1)) if match else 99
    for title, span in TIERS:
        if number in span:
            return title
    return TIERS[-1][0]


def chapter_sort_key(slug: str) -> tuple[int, str]:
    """按数字顺序排列章节，让 ``20b`` 紧跟在 ``20`` 后面。

    单纯按字母排序会把 ``20b-devui`` 排到 ``20-visualization`` *前面*，在扫视
    导航的人看来就像是编号出错。
    """
    match = re.match(r"(\d+)([a-z]*)", slug)
    if not match:
        return (99, slug)
    return (int(match.group(1)), match.group(2))


def read_title_and_body(path: Path) -> tuple[str, str]:
    """从 H1 提取标题，但保留正文中的 H1。

    主题不会自动生成同等页面标题；删除原 H1 会使页面缺少一级标题。
    元数据与可见标题都从同一原文解析，保持一致。
    """
    text = path.read_text(encoding="utf-8")
    title = path.stem.replace("-", " ").title()
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title, text


def collect_pages() -> list[Page]:
    pages: list[Page] = []

    # 首页
    # 首页正文来自可审阅的 docs/index.md，
    # 而不是由脚本合成整篇内容。
    # Jekyll 在站点根路径提供 index.md，
    # GitHub 目录浏览则展示 README.md，
    # 两者可并存。
    _, home_body = read_title_and_body(REPO_ROOT / "docs/index.md")
    pages.append(
        Page(
            source=Path("docs/index.md"),
            out_path=Path("index.md"),
            title="Home",
            nav_order=1,
            body=home_body,
        )
    )

    for section in SECTIONS:
        slug = section.title.lower().replace(" ", "-")
        if section.index_source:
            _, index_body = read_title_and_body(REPO_ROOT / section.index_source)
            pages.append(
                Page(
                    source=Path(section.index_source),
                    out_path=Path(slug) / "index.md",
                    title=section.title,
                    nav_order=section.nav_order,
                    has_children=True,
                    body=index_body,
                )
            )
        else:
            pages.append(
                Page(
                    source=Path(f"{slug}/index.md"),
                    out_path=Path(slug) / "index.md",
                    title=section.title,
                    nav_order=section.nav_order,
                    has_children=True,
                    body=section.summary,
                    generated=True,
                )
            )

        if section.title == "Concepts":
            concept_files = sorted((REPO_ROOT / "docs/concepts").glob("*.md"))
            order = 0
            for path in concept_files:
                if path.name == "README.md":
                    continue
                order += 1
                title, body = read_title_and_body(path)
                pages.append(
                    Page(
                        source=path.relative_to(REPO_ROOT),
                        out_path=Path(slug) / path.name,
                        title=title,
                        nav_order=order,
                        parent=section.title,
                        body=body,
                    )
                )
            continue

        if section.title == "Tutorials":
            for tier_order, (tier_title, _) in enumerate(TIERS, start=1):
                tier_slug = tier_title.split(" — ")[0].lower().replace(" ", "-")
                # 独立分组没有数字层级前缀，
                # 上面的分割即可得到可用路径片段。
                pages.append(
                    Page(
                        source=Path(f"{slug}/{tier_slug}.md"),
                        out_path=Path(slug) / f"{tier_slug}.md",
                        title=tier_title,
                        nav_order=tier_order,
                        parent=section.title,
                        has_children=True,
                        body=f"{tier_title} 的教程章节。",
                        generated=True,
                    )
                )

            chapter_dirs = sorted(
                (p for p in (REPO_ROOT / "tutorials").iterdir() if p.is_dir() and p.name[0].isdigit()),
                key=lambda p: chapter_sort_key(p.name),
            )
            for order, directory in enumerate(chapter_dirs, start=1):
                readme = directory / "README.md"
                if not readme.exists():
                    continue
                title, body = read_title_and_body(readme)
                pages.append(
                    Page(
                        source=readme.relative_to(REPO_ROOT),
                        out_path=Path(slug) / f"{directory.name}.md",
                        title=title,
                        nav_order=order,
                        parent=chapter_tier(directory.name),
                        grand_parent=section.title,
                        body=body,
                    )
                )
            continue

        for order, rel in enumerate(section.pages, start=1):
            path = REPO_ROOT / rel
            title, body = read_title_and_body(path)
            name = Path(rel).name
            if name == "README.md":  # 例如 docs/workflows/README.md。
                name = f"{Path(rel).parent.name}.md"
            pages.append(
                Page(
                    source=Path(rel),
                    out_path=Path(slug) / name,
                    title=title,
                    nav_order=order,
                    parent=section.title,
                    body=body,
                )
            )

    # 路径仍使用稳定英文标识；仅在页面组装完成后本地化展示标题与导航。
    display_titles = {
        "Home": "首页",
        "Getting Started": "快速开始",
        "Concepts": "核心概念",
        "Tutorials": "教程",
        "Architecture": "系统架构",
        "Guides": "操作指南",
        "Reference": "参考资料",
        "Setup": "环境配置",
        "Tier 1 — Core Agent": "第 1 层 · 智能体基础",
        "Tier 2 — Agent Internals": "第 2 层 · 智能体内部机制",
        "Tier 3 — Workflow Foundations": "第 3 层 · 工作流基础",
        "Tier 4 — Orchestrations": "第 4 层 · 编排模式",
        "Tier 5 — Advanced": "第 5 层 · 进阶能力",
        "Capstone": "完整项目导览",
        "Bonus Pattern": "补充编排模式",
        "Tier 6 — Missing Concepts": "第 6 层 · 专题能力",
        "Tier 7 — Patterns Without Production Wiring": "第 7 层 · 尚未接入生产的模式",
        "Cost Control": "成本控制",
    }
    for page in pages:
        page.title = display_titles.get(page.title, page.title)
        page.parent = display_titles.get(page.parent, page.parent)
        page.grand_parent = display_titles.get(page.grand_parent, page.grand_parent)
        if page.generated:
            for original, translated in display_titles.items():
                page.body = page.body.replace(original, translated)

    return pages


# ── 链接重写 ──────────────────────────────────────────────────────────────

LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")
# 原始 HTML 图片。docs/frontend.md 用 <table> 把两张截图并排放置，这是 markdown
# 无法表达的，所以它的 <img src> 从不经过 LINK_RE。该页面发布为
# architecture/frontend.html，而图片复制到 docs/images/，于是相对 src 会解析到
# architecture/images/，在线上 404。
HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)


@dataclass
class Rewriter:
    """把一个页面的相对链接重写为站点或 GitHub URL。"""

    by_source: dict[Path, Page]
    problems: list[str] = field(default_factory=list)

    def site_url(self, page: Page) -> str:
        out = page.out_path
        if out.name == "index.md":
            path = out.parent.as_posix()
            return "{{ site.baseurl }}/" if path == "." else f"{{{{ site.baseurl }}}}/{path}/"
        return f"{{{{ site.baseurl }}}}/{out.with_suffix('.html').as_posix()}"

    def github_url(self, rel: Path) -> str:
        target = REPO_ROOT / rel
        kind = "tree" if target.is_dir() else "blob"
        return f"{GITHUB_BASE}/{kind}/{GITHUB_BRANCH}/{rel.as_posix()}"

    def resolve(self, source: Path, target: str) -> Path | None:
        """将链接目标解析为仓库相对路径；外部链接返回 None。"""
        if target.startswith(("http://", "https://", "mailto:", "#", "{{")):
            return None
        base = (REPO_ROOT / source).parent
        try:
            return (base / target).resolve().relative_to(REPO_ROOT)
        except (ValueError, OSError):
            return None

    def rewrite(self, page: Page) -> str:
        def replace(match: re.Match[str]) -> str:
            bang, text, target, title = match.groups()
            title = title or ""
            anchor = ""
            if "#" in target and not target.startswith("#"):
                target, _, anchor = target.partition("#")
                anchor = f"#{anchor}"

            rel = self.resolve(page.source, target)
            if rel is None:
                return match.group(0)

            # 图片：与页面一起复制过去，因此保留站点路径。
            if bang or rel.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
                return f"{bang}[{text}]({{{{ site.baseurl }}}}/{rel.as_posix()}{anchor}{title})"

            # 根目录 README 就是站点首页，不是一个要向外链接的文件。
            if rel == Path("README.md"):
                return f"[{text}]({{{{ site.baseurl }}}}/{anchor}{title})"

            # 一个已发布的页面 —— 要么直接命中，要么其 README 被发布
            # （``../02-add-tools/`` 这种写法出现了 98 次）。
            for candidate in (rel, rel / "README.md"):
                if candidate in self.by_source:
                    url = self.site_url(self.by_source[candidate])
                    return f"[{text}]({url}{anchor}{title})"

            # 其余都是站点不发布的真实源码：章节代码（``./python/main.py``）、
            # ``agents/``、``scripts/``。指向 GitHub，而不是产出一个会 404 的链接。
            if not (REPO_ROOT / rel).exists():
                self.problems.append(f"{page.source}: 链接目标不存在: {target}")
                return match.group(0)
            return f"[{text}]({self.github_url(rel)}{anchor}{title})"

        def replace_html_img(match: re.Match[str]) -> str:
            head, target, tail = match.groups()
            rel = self.resolve(page.source, target)
            if rel is None:
                return match.group(0)
            if not (REPO_ROOT / rel).exists():
                self.problems.append(f"{page.source}: 图片不存在: {target}")
                return match.group(0)
            return f"{head}{{{{ site.baseurl }}}}/{rel.as_posix()}{tail}"

        return HTML_IMG_RE.sub(replace_html_img, LINK_RE.sub(replace, page.body))


# ─────────────────────────── SEO metadata ───────────────────────────────
#
# 每页应有自己的元描述，
# 不能全部使用站点配置的兜底文字。
# 否则首页、指南、概念页和教程
# 都会产生相同摘要，
# 连 Open Graph 与 JSON-LD 描述
# 也无法区分页内容，
# 降低检索结果的可辨识度。
#
# 规范地址、分享标题和站点地图
# 继续由现有 Jekyll 插件处理，
# 这里只补充页面摘要。

# 徽章、引用提示、表格、代码围栏、标题和列表
# 不能作为普通首段摘要；生成页脚也应跳过。
_SKIP_PREFIXES = ("#", ">", "|", "```", "~~~", "-", "*", "1.", "<!--", "{:", "!", "---")

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`([^`]*)`")
_MD_EMPH_RE = re.compile(r"[*_]{1,3}([^*_]+)[*_]{1,3}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_description(body: str, fallback: str) -> str:
    """将页面首个正文段落展平为元描述。

    使用原文而非另行生成摘要，减少内容漂移。
    """
    para: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if para:
                break
            continue
        if line.startswith(_SKIP_PREFIXES):
            if para:
                break
            continue
        para.append(line)

    text = " ".join(para)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_EMPH_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()

    if not text:
        return fallback

    # 约 155 个字符是 Google 截断的位置。若范围内正好有句末标点就在那里切，否则
    # 按词切 —— 绝不在词中间切，也绝不留下悬空的逗号。
    if len(text) <= 155:
        return text
    cut = text[:155]
    for stop in (". ", "? ", "! "):
        idx = cut.rfind(stop)
        if idx > 80:
            return cut[: idx + 1].strip()
    return cut[: cut.rfind(" ")].rstrip(" ,;:—-") + "…"


# 当页面确实讨论到某些概念时，值得作为关键词暴露出来。以大小写不敏感的方式
# 匹配正文，因此页面只会声明自己真正涉及的关键词 —— 一份静态的按章节关键词表
# 会把 "guardrails" 挂到全部 85 个页面上，毫无意义。
_KEYWORD_TERMS = {
    "Microsoft Agent Framework": ("microsoft agent framework", "maf"),
    "multi-agent": ("multi-agent", "multi agent"),
    "AI agents": ("agent",),
    "A2A protocol": ("a2a",),
    "MCP": ("mcp", "model context protocol"),
    "orchestration": ("orchestration", "orchestrator"),
    "workflows": ("workflow",),
    "human-in-the-loop": ("human-in-the-loop", "hitl"),
    "guardrails": ("guardrail",),
    "RAG": ("rag", "retrieval-augmented"),
    "grounding": ("grounding",),
    "evaluation": ("eval", "evaluator"),
    "observability": ("opentelemetry", "observability", "telemetry"),
    "checkpoints": ("checkpoint",),
    "Python": ("python",),
    "Azure OpenAI": ("azure openai",),
    "FastAPI": ("fastapi",),
    "PostgreSQL": ("postgres",),
    "Next.js": ("next.js",),
}


def extract_keywords(title: str, body: str, section: str | None) -> list[str]:
    """按稳定顺序提取页面实际支持的关键词。"""
    haystack = f"{title}\n{body}".lower()
    found = [kw for kw, needles in _KEYWORD_TERMS.items() if any(n in haystack for n in needles)]
    if section and section not in found:
        found.insert(0, section)
    return found[:12]


def seo_type(page: Page) -> str:
    """为 Jekyll JSON-LD 选择 schema.org 类型。

    教程和概念说明使用 TechArticle，其他页面按用途分类。
    """
    top = page.out_path.parts[0] if page.out_path.parts else ""
    return "TechArticle" if top in {"tutorials", "concepts", "guides", "architecture"} else "WebPage"


_MERMAID_FENCE_RE = re.compile(r"(^```mermaid[^\n]*\n)(.*?)(^```\s*$)", re.M | re.S)
_HEADING_RE = re.compile(r"^#{2,6}\s+(.*?)\s*$", re.M)


def label_mermaid_diagrams(body: str, page_title: str) -> str:
    """为 Mermaid 图添加可访问标题。

    用最近的前置标题生成 accTitle，让渲染 SVG 获得标题和图像角色；
    没有局部标题时使用页面标题。只补充短标题，不臆造长说明。
    """

    def label_for(offset: int) -> str:
        headings = [m.group(1) for m in _HEADING_RE.finditer(body, 0, offset)]
        raw = headings[-1] if headings else page_title
        # 指令值在换行处结束，清理 Markdown 标记，
        # 避免原样进入 SVG 标题。
        clean = _MD_CODE_RE.sub(r"\1", raw)
        clean = _MD_LINK_RE.sub(r"\1", clean)
        clean = _MD_EMPH_RE.sub(r"\1", clean)
        return _WS_RE.sub(" ", clean).strip()

    def repl(match: re.Match) -> str:
        opening, inner, closing = match.group(1), match.group(2), match.group(3)
        if "accTitle" in inner:
            return match.group(0)
        lines = inner.split("\n")
        # accTitle 必须紧跟在*图表类型*那一行之后。这里 71 张图里有 47 张以
        # `%%{init: ...}%%` 主题指令开头，若插在它后面就会把 accTitle 放到图表
        # 类型之前，Mermaid 会静默忽略它 —— 图照常渲染，只是没有标题。这个问题
        # 只有在真实浏览器里以锁定版本渲染全部 71 张图并统计 <title> 元素个数时
        # 才被发现：71 个里只有 24 个。
        in_directive = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            # `%%{init: ...}%%` 主题块在这里会跨*好几行*，且只有第一行以 `%%`
            # 开头。仅凭这个前缀判断会跳过后续行，把 accTitle 丢进 themeVariables
            # 对象中间 —— Mermaid 对此足够宽容，仍能画出图来，所以唯一的症状就是
            # 少了一个 <title>。
            if stripped.startswith("%%{"):
                in_directive = not stripped.endswith("}%%")
                continue
            if in_directive:
                if stripped.endswith("}%%"):
                    in_directive = False
                continue
            if not stripped or stripped.startswith("%%"):
                continue
            indent = line[: len(line) - len(line.lstrip())]
            # 清理冒号，避免干扰指令解析。
            label = label_for(match.start()).replace(":", " -")
            lines.insert(i + 1, f"{indent}    accTitle: {label}")
            break
        return opening + "\n".join(lines) + closing

    return _MERMAID_FENCE_RE.sub(repl, body)


@lru_cache(maxsize=None)
def git_last_modified(source: str) -> str | None:
    """页面源文件的提交日期，ISO-8601。

    jekyll-sitemap 会从 ``page.last_modified_at`` 生成 ``<lastmod>``；没有它，
    sitemap 里每个 ``<url>`` 就只带一个地址、别无他物 —— 爬虫无从判断某个章节
    是昨天刚改写还是一年没动过，重新抓取只能盲目排期。取自 git 而不是文件系统，
    因为全新克隆（持续集成环境）里所有文件的 mtime 都是检出时间，那会声称全部
    85 个页面在每次构建时同时发生变化。
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", source],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = out.stdout.strip()
    return stamp or None


def yaml_quote(value: str) -> str:
    """双引号包裹的 YAML 标量。description 是散文，经常包含冒号、引号和破折号，
    其中任何一个都会破坏未加引号的标量。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def front_matter(page: Page, body: str = "") -> str:
    lines = ["---", "layout: default", f'title: "{page.title}"', f"nav_order: {page.nav_order}"]
    if page.parent:
        lines.append(f'parent: "{page.parent}"')
    if page.grand_parent:
        lines.append(f'grand_parent: "{page.grand_parent}"')
    if page.has_children:
        lines.append("has_children: true")

    # jekyll-seo-tag 会一次性用 `description` 填充 <meta name="description">、
    # og:description 与 JSON-LD 的 description，所以这一个键同时修好了三处。
    description = extract_description(body, SITE_DESCRIPTION)
    lines.append(f"description: {yaml_quote(description)}")

    keywords = extract_keywords(page.title, body, page.parent)
    if keywords:
        lines.append(f"keywords: {yaml_quote(', '.join(keywords))}")

    lines.append(f"seo:\n  type: {seo_type(page)}")

    # 生成的章节索引没有源文件，因此合理地没有修改日期；jekyll-sitemap 对这类
    # 页面直接省略 <lastmod>。
    if not page.generated:
        stamp = git_last_modified(page.source.as_posix())
        if stamp:
            lines.append(f"last_modified_at: {stamp}")

    lines.append("---")
    return "\n".join(lines)


def source_link(page: Page) -> str:
    """每个页面回指真实文件的一个指针。

    just-the-docs 自带的「Edit this page on GitHub」已在 _config.yml 中关闭，
    因为它会指向 ``_site_src/`` —— 那个目录只存在于构建过程之中。这是它的诚实
    替代品。
    """
    if page.generated:
        return ""
    return (
        f"\n\n---\n\n*源码: "
        f"[`{page.source.as_posix()}`]({GITHUB_BASE}/blob/{GITHUB_BRANCH}/{page.source.as_posix()})"
        f" —— 本页由仓库生成。*\n"
    )


FENCE_RE = re.compile(r"^\s*(```|~~~)")


def protect_liquid(body: str) -> str:
    """把 Liquid 会啃掉的围栏代码块包进 ``{% raw %}``。

    Jekyll 会在 kramdown 判定任何内容是代码之前，先对整页跑一遍 Liquid，所以
    围栏内的 ``{{ ... }}`` 会被插值并静默替换为空。Mermaid 用 ``id{{label}}``
    表示六边形节点，这意味着 ``guard{{ReviewInjectionGuard}}`` 会发布成一个
    光秃秃的 ``guard``，标签完全消失。有四张图就是这样丢掉了节点。

    只有真正含有疑似 Liquid 结构的围栏才会被包裹，而链接重写从不在围栏内输出
    ``{{ site.baseurl }}``，所以这里没有任何东西仍需要被求值。
    """
    lines = body.split("\n")
    out: list[str] = []
    block: list[str] | None = None
    for line in lines:
        if FENCE_RE.match(line):
            if block is None:
                block = [line]
                continue
            block.append(line)
            joined = "\n".join(block)
            if "{{" in joined or "{%" in joined:
                out.extend(["{% raw %}", joined, "{% endraw %}"])
            else:
                out.append(joined)
            block = None
            continue
        (block if block is not None else out).append(line)
    if block is not None:
        # 未闭合的围栏：原样输出，而不是静默丢弃。
        out.append("\n".join(block))
    return "\n".join(out)


def check_diagram_labels(out_path: Path, body: str) -> list[str]:
    """每张图都必须带 ``accTitle``，而且必须放在正确的位置。

    值得单独做一项检查，因为这个失败是不可见的：放在图表类型行之前的
    ``accTitle`` —— 或者像这里实际发生的那样，被放进多行 ``%%{init: ...}%%``
    主题块*内部* —— 依然会渲染出一张看起来完全正常的图，只是不会产出任何
    ``<title>``。页面看不出任何问题；只是对使用屏幕阅读器的人来说，这张图没有
    标签。

    这正是本函数存在的意义所在：``label_mermaid_diagrams`` 的第一个版本只给
    71 张图中的 24 张加了标签，而在任何渲染出来的页面里看起来都是对的。它是靠
    在真实浏览器中以锁定的 Mermaid 版本渲染全部 71 张图并统计 ``<title>`` 元素
    才被发现的。
    """
    problems: list[str] = []
    for match in _MERMAID_FENCE_RE.finditer(body):
        inner = match.group(2)
        lines = inner.split("\n")
        if "accTitle:" not in inner:
            problems.append(f"{out_path}: 有一张 mermaid 图缺少 accTitle")
            continue
        # 用与注入器相同的方式找到图表类型行，然后要求 accTitle 在它之后。
        in_directive = False
        type_index = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("%%{"):
                in_directive = not stripped.endswith("}%%")
                continue
            if in_directive:
                if stripped.endswith("}%%"):
                    in_directive = False
                continue
            if not stripped or stripped.startswith("%%"):
                continue
            type_index = i
            break
        acc_index = next((i for i, line in enumerate(lines) if line.strip().startswith("accTitle:")), None)
        if type_index is None or acc_index is None or acc_index <= type_index:
            problems.append(
                f"{out_path}: accTitle 不在图表类型行之后，因此 Mermaid 会忽略它，图表渲染出来后没有可访问的标题"
            )
    return problems


def ensure_h1(body: str, title: str) -> str:
    """确保页面以 H1 开头。

    从源文件读取的页面本来就带有 H1。生成的章节索引则没有 —— 它们的正文是一段
    硬编码的摘要字符串 —— 所以没有这个函数，即便去掉了剥离逻辑，它们仍会继续
    发布没有标题的页面。

    只有*位于开头*的 H1 才算数。首个标题是 H2 的页面没有顶层标题，此时补上一个
    才是修复，而不是重复。
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return body if stripped.startswith("# ") else f"# {title}\n\n{body}"
    return f"# {title}\n\n{body}"


# 生成 llms.txt 索引。
#
# 写它是因为实测证据要求这么做。在 14 天的窗口内，chatgpt.com 给本仓库带来了
# 114 次浏览 —— 比 Google（58）或 Bing（61）单独一个都多。发现正在通过模型回答
# 发生，而站点此前没有发布任何为此而组织的内容。
#
# 按 llmstxt.org 的约定写两个文件：
#   llms.txt       索引：每个页面、它的 URL，以及一行描述
#   llms-full.txt  所有页面正文拼接在一起，一次抓取就能把一个模型的整个语料
#                  给它，而不用来回 87 次
#
# 两者都在 build() 清空 OUT_DIR 之后才写，否则它们会在被提供之前就被删掉。

SITE_ORIGIN = "https://s1encisi.github.io"
SITE_BASEURL = "/reliable-commerce-agents"


def page_url(page: Page) -> str:
    """页面的公开 URL，与 Jekyll 的 .md -> .html 映射保持一致。"""
    path = page.out_path.as_posix()
    if path.endswith("index.md"):
        path = path[: -len("index.md")]
    elif path.endswith(".md"):
        path = path[: -len(".md")] + ".html"
    return f"{SITE_ORIGIN}{SITE_BASEURL}/{path}"


def strip_for_plaintext(body: str) -> str:
    """去掉对阅读语料的模型而言只是噪声的 Liquid 脚手架。"""
    body = re.sub(r"\{%\s*raw\s*%\}|\{%\s*endraw\s*%\}", "", body)
    # {{ site.baseurl }}/foo -> 真实 URL，让语料里的链接可以解析。
    body = re.sub(r"\{\{\s*site\.baseurl\s*\}\}", SITE_BASEURL, body)
    body = re.sub(r"\n\{:\s*\.[^}]*\}", "", body)  # just-the-docs 提示标记
    return body.strip()


def write_llms_files(pages: list[Page], rendered: dict[Path, str]) -> None:
    ordered = sorted(pages, key=lambda p: (p.parent or "", p.nav_order, p.title))

    by_section: dict[str, list[Page]] = {}
    for page in ordered:
        by_section.setdefault(page.parent or page.title, []).append(page)

    index = [
        "# 可靠电商多智能体平台",
        "",
        f"> {SITE_DESCRIPTION}",
        "",
        "一套基于微软智能体框架（Microsoft Agent Framework）构建的多智能体电商平台，",
        "后端为 Python，前端为单个 Next.js 应用，五种编排模式可在运行时选择。",
        "本文档由仓库生成，因此每个页面都对应仓库中的一个文件。",
        "",
        f"仓库: https://github.com/s1encisi/reliable-commerce-agents",
        f"所有页面的全文: {SITE_ORIGIN}{SITE_BASEURL}/llms-full.txt",
        "",
    ]
    for section, section_pages in by_section.items():
        index.append(f"## {section}")
        index.append("")
        for page in section_pages:
            body = rendered.get(page.out_path, "")
            parts = body.split("---", 2)
            desc = ""
            if len(parts) > 2:
                match = re.search(r'^description:\s*"?(.*?)"?\s*$', parts[1], re.MULTILINE)
                desc = match.group(1) if match else ""
            index.append(f"- [{page.title}]({page_url(page)}){': ' + desc if desc else ''}")
        index.append("")

    (OUT_DIR / "llms.txt").write_text("\n".join(index) + "\n", encoding="utf-8")

    full = [
        "# 可靠电商多智能体平台 —— 完整文档",
        "",
        f"由 https://github.com/s1encisi/reliable-commerce-agents 生成",
        f"索引: {SITE_ORIGIN}{SITE_BASEURL}/llms.txt",
        "",
        "---",
        "",
    ]
    for page in ordered:
        body = rendered.get(page.out_path, "")
        # 去掉 YAML front matter；那是 Jekyll 的东西，不是内容。
        parts = body.split("---", 2)
        content = parts[2] if len(parts) > 2 else body
        full.append(f"# {page.title}")
        full.append("")
        full.append(f"源码: {page_url(page)}")
        full.append("")
        full.append(strip_for_plaintext(content))
        full.append("")
        full.append("---")
        full.append("")

    (OUT_DIR / "llms-full.txt").write_text("\n".join(full) + "\n", encoding="utf-8")

    (OUT_DIR / "robots.txt").write_text(
        "\n".join(
            [
                "User-agent: *",
                "Allow: /",
                "",
                f"Sitemap: {SITE_ORIGIN}{SITE_BASEURL}/sitemap.xml",
                "",
                "# 面向语言模型组织 —— 参见 https://llmstxt.org",
                f"# 索引:     {SITE_ORIGIN}{SITE_BASEURL}/llms.txt",
                f"# 全文:     {SITE_ORIGIN}{SITE_BASEURL}/llms-full.txt",
                "",
            ]
        ),
        encoding="utf-8",
    )

    size_kb = (OUT_DIR / "llms-full.txt").stat().st_size / 1024
    print(f"已写出 llms.txt（{len(ordered)} 个页面）、llms-full.txt（{size_kb:.0f} KB）、robots.txt")


def build(check_only: bool) -> int:
    pages = collect_pages()
    by_source = {p.source: p for p in pages if not p.generated}
    rewriter = Rewriter(by_source=by_source)

    rendered: dict[Path, str] = {}
    for page in pages:
        body = rewriter.rewrite(page) if not page.generated else page.body
        # 顺序很重要：先打标签再 protect_liquid，这样注入的 accTitle 会落在
        # {% raw %} 包裹之内，而不是悬在它外面。
        body = ensure_h1(body, page.title)
        body = label_mermaid_diagrams(body, page.title)
        body = protect_liquid(body)
        rendered[page.out_path] = f"{front_matter(page, body)}\n\n{body}{source_link(page)}"

        # 一个 description 回退到站点默认值的页面，在搜索引擎看来就是另外 84 个
        # 页面的重复。正是这项检查阻止了最初的缺陷静默复发。
        if SITE_DESCRIPTION in rendered[page.out_path].split("---", 2)[1]:
            rewriter.problems.append(
                f"{page.out_path}: 没有可用的开篇段落，因此它的 meta description "
                f"回退到了站点默认值（SEO 整改前全部 85 个页面都共用它）"
            )

        rewriter.problems.extend(check_diagram_labels(page.out_path, body))

    duplicate_titles: dict[tuple[str | None, str], list[str]] = {}
    for page in pages:
        duplicate_titles.setdefault((page.parent, page.title), []).append(page.out_path.as_posix())
    for (parent, title), where in duplicate_titles.items():
        if len(where) > 1:
            # just-the-docs 按*标题*匹配父子关系，所以同一父节点下两个页面
            # 重名会让导航静默塌陷。
            rewriter.problems.append(f"父节点 {parent!r} 下存在重复标题 {title!r}: {', '.join(where)}")

    known_parents = {p.title for p in pages if p.has_children}
    for page in pages:
        for rel, kind in ((page.parent, "parent"), (page.grand_parent, "grand_parent")):
            if rel and rel not in known_parents:
                rewriter.problems.append(f"{page.out_path}: {kind} {rel!r} 没有任何页面声明 has_children")

    if rewriter.problems:
        print(f"{len(rewriter.problems)} 个问题:", file=sys.stderr)
        for problem in sorted(set(rewriter.problems)):
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if check_only:
        print(f"ok: {len(pages)} 个页面，其中 {sum(1 for p in pages if p.generated)} 个为生成页，没有断链")
        return 0

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    shutil.copy(REPO_ROOT / "docs/_config.yml", OUT_DIR / "_config.yml")

    # `_includes/` 必须落在站点根目录，而不是 `docs/` 之下。这就是为什么它是
    # 单独一次复制，而不是下面资源循环里的一个条目：那个循环会保留 `docs/`
    # 前缀，而 Jekyll 只在顶层查找 `_includes/` —— 否则它会静默地什么都不渲染。
    shutil.copytree(REPO_ROOT / "docs/_includes", OUT_DIR / "_includes")
    for asset in ("docs/images", "docs/architecture.png"):
        src = REPO_ROOT / asset
        if not src.exists():
            continue
        dest = OUT_DIR / asset
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy(src, dest)

    for out_path, text in rendered.items():
        target = OUT_DIR / out_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    write_llms_files(pages, rendered)

    mermaid = sum(text.count("```mermaid") for text in rendered.values())
    print(f"已把 {len(rendered)} 个页面构建到 {OUT_DIR.relative_to(REPO_ROOT)}/（{mermaid} 张 mermaid 图）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()
    return build(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
