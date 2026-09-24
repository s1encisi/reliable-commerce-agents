#!/usr/bin/env python
"""将 YAML 工作流规格渲染为 Mermaid 和 Graphviz DOT。

默认读取 agents/python/config/workflows，输出到 docs/workflows。
--specs 和 --out 可覆盖目录；--check 只检测产物漂移，存在差异时
返回非零退出码，不改写文件。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPECS = REPO_ROOT / "agents" / "python" / "config" / "workflows"
DEFAULT_OUT = REPO_ROOT / "docs" / "workflows"

# 直接执行脚本时，让 shared 模块可导入。
sys.path.insert(0, str(REPO_ROOT / "agents" / "python"))

from agent_framework._workflows._viz import WorkflowViz  # noqa: E402
from shared.workflow_loader import load_workflows_directory  # noqa: E402

logger = logging.getLogger("visualize_workflows")


def render_all(specs_dir: Path, out_dir: Path) -> dict[str, tuple[str, str]]:
    """加载全部规格，返回名称到 Mermaid/DOT 文本的映射，不写文件。"""
    workflows = load_workflows_directory(specs_dir)
    rendered: dict[str, tuple[str, str]] = {}
    for name, workflow in workflows.items():
        viz = WorkflowViz(workflow)
        rendered[name] = (viz.to_mermaid(), viz.to_digraph())
    return rendered


def write_rendered(rendered: dict[str, tuple[str, str]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, (mermaid, dot) in rendered.items():
        mmd_path = out_dir / f"{name}.mmd"
        dot_path = out_dir / f"{name}.dot"
        mmd_path.write_text(mermaid)
        dot_path.write_text(dot)
        written.append(mmd_path)
        written.append(dot_path)
    return written


def check_drift(rendered: dict[str, tuple[str, str]], out_dir: Path) -> list[tuple[str, str]]:
    """返回描述漂移的 ``(path, problem)`` 列表。

    返回空列表表示磁盘上已提交的文件与加载器当前重新生成的结果一致。
    """
    problems: list[tuple[str, str]] = []

    # 每个被渲染的工作流都必须有对应的两个文件在磁盘上，且内容一致。
    for name, (mermaid, dot) in rendered.items():
        for suffix, expected in [(".mmd", mermaid), (".dot", dot)]:
            path = out_dir / f"{name}{suffix}"
            if not path.is_file():
                problems.append((str(path), "缺失"))
                continue
            actual = path.read_text()
            if actual != expected:
                problems.append((str(path), "内容漂移"))

    # 磁盘上已不再对应任何 spec 的文件即为陈旧文件。
    if out_dir.is_dir():
        expected_names = {f"{name}{suffix}" for name in rendered for suffix in (".mmd", ".dot")}
        for existing in out_dir.iterdir():
            if existing.name.startswith("."):
                continue
            if existing.suffix in {".mmd", ".dot"} and existing.name not in expected_names:
                problems.append((str(existing), "orphan — no spec produces this file"))

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--specs", type=Path, default=DEFAULT_SPECS, help=f"spec 目录（默认: {DEFAULT_SPECS}）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"输出目录（默认: {DEFAULT_OUT}）")
    parser.add_argument("--check", action="store_true", help="若输出文件与 spec 发生漂移则失败")
    parser.add_argument("--quiet", "-q", action="store_true", help="仅记录错误")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.specs.is_dir():
        logger.warning("no specs directory at %s — nothing to render", args.specs)
        return 0

    rendered = render_all(args.specs, args.out)

    if not rendered:
        logger.info("no workflow specs found under %s", args.specs)
        return 0

    if args.check:
        problems = check_drift(rendered, args.out)
        if problems:
            logger.error("Workflow visualization drift detected:")
            for path, reason in problems:
                logger.error("  %s — %s", path, reason)
            logger.error(
                "Regenerate with: python scripts/visualize_workflows.py"
            )
            return 1
        logger.info("%d workflow(s) match committed diagrams", len(rendered))
        return 0

    written = write_rendered(rendered, args.out)
    logger.info("rendered %d workflow(s), wrote %d files to %s", len(rendered), len(written), args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
