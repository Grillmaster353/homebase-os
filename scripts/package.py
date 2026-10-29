#!/usr/bin/env python3
"""Build a portable source bundle using an explicit source allowlist.

Runtime data, credentials, certificates, workspace files, caches and symlinks are
never included. Run after source changes to regenerate the release.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent
TOP_LEVEL_FILES = (
    "README.md", "START-HERE.md", "CONTRACT.md", "requirements.txt", "Dockerfile",
    "compose.yaml", "compose.lan.yaml", ".gitignore", ".dockerignore", "requirements-tv.txt", "requirements-docs.txt",
    "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
    ".github/workflows/ci.yml", ".github/workflows/pages.yml", "docs/.nojekyll",
)
SOURCE_DIRECTORIES = ("app", "web", "scripts", "tests", "docs")
ALLOWED_SUFFIXES = {
    ".py", ".sh", ".html", ".css", ".js", ".mjs", ".svg", ".png",
    ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".md",
}
BLOCKED_PARTS = {"__pycache__", ".data", "data", "workspace", ".venv", "certs", ".setup-ca", ".git"}


def source_files(root: Path):
    """Yield only known source paths, refusing symlinks at every level."""
    for name in TOP_LEVEL_FILES:
        path = root / name
        if path.is_file() and not any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
            yield path
    for name in SOURCE_DIRECTORIES:
        folder = root / name
        if not folder.is_dir() or folder.is_symlink():
            continue
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in BLOCKED_PARTS or part.startswith(".") for part in relative.parts):
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
                continue
            yield path


def build_archive(root: Path, destination: Path) -> tuple[Path, str]:
    root = root.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        for path in source_files(root):
            archive.add(path, arcname=str(Path("home-command") / path.relative_to(root)), recursive=False)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    return destination, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "releases" / "home-command-source.tar.gz")
    args = parser.parse_args()
    path, digest = build_archive(ROOT, args.output)
    print(f"Source bundle: {path}")
    print(f"SHA-256: {digest}")
    print("Contains source only; runtime credentials, encryption keys and workspace files are excluded.")


if __name__ == "__main__":
    main()
