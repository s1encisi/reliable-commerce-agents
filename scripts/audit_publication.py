"""Build an allowlisted source snapshot and run Gitleaks without printing secrets."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOTS = {
    ".git",
    ".claude",
    ".codex",
    ".agents",
    ".local",
    "data",
    "datasets",
    "exports",
    "reports",
    "results",
    "private",
    "confidential",
    "secrets",
    "local-only",
}
PRIVATE_SUFFIXES = {
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".parquet",
    ".sqlite",
    ".db",
    ".dump",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".docx",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".bundle",
    ".pt",
    ".pth",
    ".onnx",
}
ENV_TEMPLATES = {".env.example", ".env.minimal"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ref", default="HEAD", help="Only the history that will be published"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".local/publication-audit"
    )
    args = parser.parse_args()
    if not any(
        args.output.resolve().is_relative_to(ROOT / d) for d in [".local", ".claude"]
    ):
        raise SystemExit("Audit output must stay in an ignored local directory")
    args.output.mkdir(parents=True, exist_ok=True)
    revision = (
        git("rev-parse", "--verify", "--end-of-options", args.ref + "^{commit}")
        .decode()
        .strip()
    )
    tree = args.output / "tree"
    if tree.exists():
        shutil.rmtree(tree)  # only this script's generated snapshot
    tree.mkdir()
    files = sorted(
        set(
            git("ls-files", "-c", "-o", "--exclude-standard", "-z").decode().split("\0")
        )
        - {""}
    )
    history = set(
        git("log", revision, "--format=", "--name-only").decode().splitlines()
    ) - {""}
    forbidden = []
    for name in sorted(set(files) | history):
        p = Path(name)
        if (
            p.parts[0].lower() in PRIVATE_ROOTS
            or p.suffix.lower() in PRIVATE_SUFFIXES
            or (p.name.lower().startswith(".env") and name not in ENV_TEMPLATES)
            or (p.suffix.lower() == ".sql" and not name.startswith("docker/postgres/"))
        ):
            forbidden.append(name)
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"],
        cwd=ROOT,
        input="\0".join(sorted(set(files) | history)).encode() + b"\0",
        capture_output=True,
    )
    if ignored.returncode not in {0, 1}:
        raise SystemExit("Could not check the publication ignore boundary")
    forbidden.extend(name for name in ignored.stdout.decode().split("\0") if name)
    forbidden = sorted(set(forbidden))
    symlinks = []
    for name in files:
        source = ROOT / name
        if source.is_symlink():
            symlinks.append(name)
            continue
        if not source.is_file():
            continue
        destination = tree / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    scanner = shutil.which("gitleaks") or str(ROOT / ".local/bin/gitleaks")
    scans = {}
    for kind, command in {
        "history": [scanner, "git", str(ROOT), f"--log-opts={revision}"],
        "snapshot": [scanner, "dir", str(tree)],
    }.items():
        report = args.output / f"{kind}.json"
        result = subprocess.run(
            command
            + [
                "--redact=100",
                "--ignore-gitleaks-allow",
                "--no-banner",
                "--report-format",
                "json",
                "--report-path",
                str(report),
            ],
            capture_output=True,
            text=True,
        )
        (args.output / f"{kind}.log").write_text(result.stdout + result.stderr)
        scans[kind] = {
            "exit_code": result.returncode,
            "findings": len(json.loads(report.read_text()))
            if report.exists()
            else None,
        }
    summary = {
        "ref": args.ref,
        "source_files": len(files),
        "historical_paths": len(history),
        "forbidden_paths": forbidden,
        "symlinks_requiring_review": symlinks,
        "scans": scans,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "files": len(files),
                "forbidden_paths": len(forbidden),
                "symlinks": len(symlinks),
                "scans": scans,
            }
        )
    )
    raise SystemExit(
        1 if forbidden or symlinks or any(v["exit_code"] for v in scans.values()) else 0
    )


if __name__ == "__main__":
    main()
