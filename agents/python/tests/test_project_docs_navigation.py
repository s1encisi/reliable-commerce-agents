"""Main product docs must be reachable inside the generated site, not only exist on disk."""

import importlib.util
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("project_docs_builder", ROOT / "scripts/build_docs_site.py")
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)

# Reader journeys promised by the project homepage, not the generator's navigation implementation.
REQUIRED = {
    Path("docs/portfolio-demo.md"),
    Path("docs/after-sales-architecture.md"),
    Path("docs/verification.md"),
    Path("docs/task-workspace.md"),
    Path("docs/procurement-demo.md"),
    Path("docs/procurement-architecture.md"),
    Path("docs/procurement-evaluation.md"),
    Path("docs/retrieval-evaluation.md"),
    Path("docs/fulfillment-simulation.md"),
    Path("docs/local-showcase.md"),
    Path("docs/local-telemetry-verification.md"),
    Path("docs/portfolio-interview.md"),
    Path("docs/local-privacy.md"),
    Path("CONTRIBUTING.md"),
}


def test_core_journeys_are_reachable_from_home_without_leaving_site():
    pages = builder.collect_pages()
    by_source = {page.source: page for page in pages if not page.generated}
    rewriter = builder.Rewriter(by_source)
    by_url = {rewriter.site_url(page): page for page in pages}
    pending = [by_source[Path("docs/index.md")]]
    visited = set()
    while pending:
        page = pending.pop()
        if page.source in visited:
            continue
        visited.add(page.source)
        body = rewriter.rewrite(page) if not page.generated else page.body
        for url in re.findall(r"\]\((\{\{ site\.baseurl \}\}/[^)]*)\)", body):
            target = by_url.get(url.split("#", 1)[0])
            if target:
                pending.append(target)
    assert REQUIRED <= visited, f"Not reachable from home within site: {REQUIRED - visited}"
    assert not rewriter.problems


def test_site_build_writes_core_pages_with_internal_navigation(monkeypatch):
    # The production generator writes beneath REPO_ROOT; keep the test within that contract.
    scratch = ROOT / ".local"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="docs-contract-", dir=scratch) as temporary:
        output = Path(temporary) / "generated-site"
        monkeypatch.setattr(builder, "OUT_DIR", output)
        assert builder.build(check_only=False) == 0
        pages = {page.source: page for page in builder.collect_pages()}
        for source in REQUIRED:
            assert (output / pages[source].out_path).is_file(), source
        home = (output / "index.md").read_text()
        project = (output / "project/index.md").read_text()
        assert "{{ site.baseurl }}/project/" in home
        assert "{{ site.baseurl }}/project/task-workspace.html" in project
        assert "{{ site.baseurl }}/project/fulfillment-simulation.html" in project
        assert "{{ site.baseurl }}/project/local-showcase.html" in project
        assert "{{ site.baseurl }}/project/local-telemetry-verification.html" in project
