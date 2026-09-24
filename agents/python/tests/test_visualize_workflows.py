"""工作流可视化脚本测试，在隔离临时目录中覆盖辅助函数与命令行入口。"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "visualize_workflows.py"


# 按文件路径导入，脚本未安装为包。
_spec = importlib.util.spec_from_file_location("visualize_workflows", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["visualize_workflows"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]


SIMPLE_SPEC = textwrap.dedent(
    """\
    name: tiny
    start: head
    executors:
      - id: head
        op: upper
      - id: tail
        op: prefix
        prefix: "END: "
    edges:
      - from: head
        to: tail
    """
)


@pytest.fixture
def specs_dir(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "tiny.yaml").write_text(SIMPLE_SPEC)
    return d


@pytest.fixture
def out_dir(tmp_path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


def test_render_all_returns_mermaid_and_dot(specs_dir) -> None:
    rendered = _module.render_all(specs_dir, Path("/tmp/ignored"))
    assert "tiny" in rendered
    mermaid, dot = rendered["tiny"]
    assert mermaid.startswith("flowchart")
    assert dot.startswith("digraph")


def test_write_rendered_creates_two_files_per_workflow(specs_dir, out_dir) -> None:
    rendered = _module.render_all(specs_dir, out_dir)
    written = _module.write_rendered(rendered, out_dir)
    assert len(written) == 2
    assert (out_dir / "tiny.mmd").is_file()
    assert (out_dir / "tiny.dot").is_file()


def test_check_drift_passes_when_files_match(specs_dir, out_dir) -> None:
    rendered = _module.render_all(specs_dir, out_dir)
    _module.write_rendered(rendered, out_dir)
    assert _module.check_drift(rendered, out_dir) == []


def test_check_drift_reports_content_changes(specs_dir, out_dir) -> None:
    rendered = _module.render_all(specs_dir, out_dir)
    _module.write_rendered(rendered, out_dir)
    (out_dir / "tiny.mmd").write_text("## tampered")

    problems = _module.check_drift(rendered, out_dir)
    assert any("内容漂移" in reason for _, reason in problems)


def test_check_drift_reports_missing_files(specs_dir, out_dir) -> None:
    rendered = _module.render_all(specs_dir, out_dir)  # 有意不写入文件。
    problems = _module.check_drift(rendered, out_dir)
    assert any("缺失" in reason for _, reason in problems)
    assert len(problems) == 2  # Mermaid 和 DOT 产物均缺失。


def test_check_drift_flags_orphan_files(specs_dir, out_dir) -> None:
    rendered = _module.render_all(specs_dir, out_dir)
    _module.write_rendered(rendered, out_dir)
    (out_dir / "orphan.mmd").write_text("abandoned")

    problems = _module.check_drift(rendered, out_dir)
    assert any("orphan" in reason for _, reason in problems)


def test_main_regenerates_files(specs_dir, out_dir) -> None:
    exit_code = _module.main(["--specs", str(specs_dir), "--out", str(out_dir), "--quiet"])
    assert exit_code == 0
    assert (out_dir / "tiny.mmd").is_file()
    assert (out_dir / "tiny.dot").is_file()


def test_main_check_succeeds_after_regenerate(specs_dir, out_dir) -> None:
    _module.main(["--specs", str(specs_dir), "--out", str(out_dir), "--quiet"])
    exit_code = _module.main(["--specs", str(specs_dir), "--out", str(out_dir), "--quiet", "--check"])
    assert exit_code == 0


def test_main_check_fails_on_drift(specs_dir, out_dir) -> None:
    _module.main(["--specs", str(specs_dir), "--out", str(out_dir), "--quiet"])
    (out_dir / "tiny.mmd").write_text("## tampered")
    exit_code = _module.main(["--specs", str(specs_dir), "--out", str(out_dir), "--quiet", "--check"])
    assert exit_code == 1


def test_main_noops_when_specs_dir_missing(tmp_path) -> None:
    exit_code = _module.main(["--specs", str(tmp_path / "nope"), "--out", str(tmp_path), "--quiet"])
    assert exit_code == 0  # 无规格时无需渲染，不视为错误。
