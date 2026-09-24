#!/usr/bin/env python
"""把 tutorials/*/README.md 导出到 Hugo 博客仓库。

读取每个章节已经整理好的 README，将其 front matter 重塑为 s1encisi.github.io 上
Blowfish 主题所期望的格式，并以带日期的文件名写入 Hugo 仓库的 ``content/posts/``。

在仓库根目录运行::

    python scripts/migrate_tutorials_to_hugo.py --dry-run
    python scripts/migrate_tutorials_to_hugo.py --force

所有文章都以 ``draft: true`` 写出，便于作者在每章的视频演示录制完成后再逐篇放出。
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TUTORIALS = REPO_ROOT / "tutorials"
HUGO_POSTS = Path(
    os.environ.get("HUGO_POSTS_DIR", str(REPO_ROOT / "dist/hugo-posts"))
)
GITHUB_BASE = "https://github.com/s1encisi/reliable-commerce-agents/tree/main/tutorials"

# 每周一篇的发布节奏，从上一轮 Python 系列文章之后的那个周一开始
# （2026-07-21 → 下一个周二 2026-07-28）。
FIRST_PUBLISH = date(2026, 7, 28)

# 「已被取代」横幅的映射。键是旧文章的 slug（日期前缀之后的文件名），
# 值是用来取代它的新系列章节 slug 列表。
SUPERSEDED_BY = {
    "part-01-agents-concepts-and-first-implementation": ["01-first-agent", "02-add-tools"],
    "part-02-prompt-engineering-for-agents": ["01-first-agent", "05-context-providers"],
    "part-03-building-domain-specific-tools": ["02-add-tools"],
    "part-04-multi-agent-orchestration-a2a": ["14-handoff-orchestration", "21-capstone-tour"],
    "part-05-observability-opentelemetry": ["07-observability-otel"],
    "part-06-frontend-rich-cards-streaming": ["03-streaming-and-multiturn"],
    "part-07-production-auth-rbac-deployment": ["06-middleware"],
    "part-08-agent-memory": ["04-sessions", "05-context-providers"],
    "part-09-evaluation-framework": ["21-capstone-tour"],
    "part-10-mcp-integration": ["08-mcp-tools"],
    "part-11-graph-based-workflows": ["09-workflow-executors-and-edges", "10-workflow-events-and-builder"],
}


def parse_frontmatter(markdown: str) -> tuple[dict, str]:
    """把一份 Hugo markdown 拆成 (front matter 字典, 正文)。"""
    if not markdown.startswith("---\n"):
        raise ValueError("缺少起始的 --- 分隔符")
    end = markdown.find("\n---\n", 4)
    if end < 0:
        raise ValueError("缺少结束的 --- 分隔符")
    yaml_text = markdown[4:end]
    body = markdown[end + len("\n---\n") :]
    return yaml.safe_load(yaml_text), body


def render_frontmatter(fm: dict) -> str:
    """按现有 Hugo 文章使用的顺序渲染 front matter。"""
    ordered_keys = [
        "draft",
        "title",
        "description",
        "authors",
        "date",
        "lastmod",
        "type",
        "categories",
        "tags",
        "series",
        "series_order",
        "slug",
        "cover",
        "coverAlt",
        "toc",
        "mermaid",
    ]
    ordered = {}
    for key in ordered_keys:
        if key in fm:
            ordered[key] = fm[key]
    for key, value in fm.items():
        if key not in ordered:
            ordered[key] = value
    body = yaml.safe_dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    ).rstrip("\n")
    return f"---\n{body}\n---\n"


def chapter_chapters() -> list[Path]:
    return sorted(p for p in TUTORIALS.glob("[0-9][0-9]-*") if p.is_dir())


def transform(chapter_dir: Path, series_order: int, pub_date: date) -> tuple[str, str]:
    """返回单个章节的 ``(文件名, 渲染后的 markdown)``。"""
    source = chapter_dir / "README.md"
    fm, body = parse_frontmatter(source.read_text(encoding="utf-8"))

    slug = chapter_dir.name  # "00-setup" → 原样保留，这样 URL 会读作 /posts/maf-v1-00-setup/
    body = body.strip("\n") + "\n"

    # Blowfish 风格的 front matter。
    new_fm: dict = {
        "draft": True,
        "title": fm.get("title", chapter_dir.name),
        "description": fm.get("summary", ""),
        "authors": ["aria"],
        "date": pub_date.isoformat(),
        "lastmod": pub_date.isoformat(),
        "type": "deep-dive",
        "categories": sorted(set((fm.get("categories") or []) + ["Deep Dive", "AI Engineering"])),
        "tags": fm.get("tags") or [],
        "series": fm.get("series") or ["MAF v1: Python"],
        "series_order": series_order,
        "slug": f"maf-v1-{slug}",
    }

    cover = fm.get("cover")
    if isinstance(cover, dict):
        new_fm["cover"] = cover.get("image", f"img/posts/maf-v1-{slug}.jpg")
        if cover.get("alt"):
            new_fm["coverAlt"] = cover["alt"]
    elif isinstance(cover, str):
        new_fm["cover"] = cover

    new_fm["toc"] = fm.get("toc", True)
    if "mermaid" in fm:
        new_fm["mermaid"] = fm["mermaid"]

    # GitHub 交叉链接：插在系列说明之后，或正文开头。
    github_link = (
        f"\n> **仓库** — 本章完整可运行代码位于 "
        f"[{GITHUB_BASE}/{slug}]({GITHUB_BASE}/{slug})。"
        f"克隆仓库、`cd tutorials/{slug}`，然后照着 README 操作即可。\n"
    )

    # 把仓库链接紧跟在第一个引用块（「系列说明」）之后插入。
    body_with_link = body.lstrip()
    if body_with_link.startswith("> "):
        # 找到引用块的结尾
        paragraph_end = body_with_link.find("\n\n")
        if paragraph_end > 0:
            body_with_link = (
                body_with_link[: paragraph_end + 1] + github_link + body_with_link[paragraph_end + 1 :]
            )
        else:
            body_with_link = body_with_link + "\n" + github_link
    else:
        body_with_link = github_link.lstrip() + "\n" + body_with_link

    filename = f"{pub_date.isoformat()}-maf-v1-{slug}.md"
    return filename, render_frontmatter(new_fm) + "\n" + body_with_link


def find_old_post(slug: str, hugo_posts: Path) -> Path | None:
    for candidate in hugo_posts.iterdir():
        if candidate.name.endswith(f"-{slug}.md"):
            return candidate
    return None


def inject_superseded_banner(old_path: Path, new_slugs: list[str]) -> bool:
    """在旧文章的 front matter 之下插入「已有更新版本」横幅。"""
    text = old_path.read_text(encoding="utf-8")
    marker = "**本文已有更新版本**"
    if marker in text:
        return False
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    if end < 0:
        return False

    banner_links = ", ".join(
        f"[MAF v1 — {s}](/posts/maf-v1-{s}/)" for s in new_slugs
    )
    banner = (
        f"\n> **本文已有更新版本** — 这篇更完整的新版文章是 "
        f"*MAF v1: Python* 系列的一部分：{banner_links}。\n"
    )
    new_text = text[: end + len("\n---\n")] + banner + text[end + len("\n---\n") :]
    old_path.write_text(new_text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印目标，不写文件")
    parser.add_argument("--force", action="store_true", help="覆盖同名的已有文章")
    parser.add_argument("--hugo-posts", type=Path, default=HUGO_POSTS, help="Hugo 的 content/posts 目录")
    parser.add_argument("--skip-banners", action="store_true", help="不触碰旧的仅 Python 文章")
    args = parser.parse_args(argv)

    chapters = chapter_chapters()
    print(f"共找到 {len(chapters)} 个教程章节。")
    if not chapters:
        return 0

    args.hugo_posts.mkdir(parents=True, exist_ok=True)

    for index, chapter in enumerate(chapters):
        pub_date = FIRST_PUBLISH + timedelta(weeks=index)
        filename, body = transform(chapter, series_order=index, pub_date=pub_date)
        target = args.hugo_posts / filename
        if args.dry_run:
            print(f"  将写出 {target}（{len(body)} 字符）")
            continue
        if target.exists() and not args.force:
            print(f"  跳过（已存在）: {target}")
            continue
        target.write_text(body, encoding="utf-8")
        print(f"  已写出 {target}")

    if args.skip_banners or args.dry_run:
        return 0

    print("\n正在为旧的仅 Python 文章注入「已有更新版本」横幅…")
    for slug, new_slugs in SUPERSEDED_BY.items():
        old = find_old_post(slug, args.hugo_posts)
        if old is None:
            print(f"  （未找到）{slug}")
            continue
        if inject_superseded_banner(old, new_slugs):
            print(f"  已添加横幅 → {old.name}")
        else:
            print(f"  已有横幅: {old.name}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
