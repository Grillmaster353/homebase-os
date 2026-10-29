#!/usr/bin/env python3
"""Export the public Raspberry Pi/Docker profile without runtime or local data.

The destination must be new or empty. The source is read-only; no repository is
created remotely and no publication, installation or device action occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = (
    "Dockerfile", "compose.yaml", "compose.lan.yaml", "requirements.txt",
    "requirements-tv.txt", "requirements-docs.txt", ".gitignore", ".dockerignore", "LICENSE",
    "SECURITY.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md",
)
ROOT_REPLACEMENTS = {
    "README.md": "docs/public/README.md",
    "START-HERE.md": "docs/public/START-HERE.md",
}
SCRIPTS = ("scripts/setup-pi.sh", "scripts/package.py", "scripts/export-github.py", "scripts/build-docs.py")
WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/pages.yml")
TREE_TYPES = {
    "app": {".py"},
    "web": {".html", ".css", ".js", ".mjs", ".svg", ".woff", ".woff2", ".md"},
    "tests": {".py", ".mjs"},
    "docs": {".html", ".css", ".js", ".svg", ".md"},
}
BLOCKED_PARTS = {
    ".git", ".data", "data", "workspace", "certs", ".setup-ca", ".venv",
    "__pycache__", ".pytest_cache", "releases", "node_modules", "screenshots",
}
REQUIRED = (
    "Dockerfile", "compose.yaml", "compose.lan.yaml", "requirements.txt", "requirements-tv.txt",
    "README.md", "START-HERE.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "requirements-docs.txt", "scripts/build-docs.py",
    "THIRD_PARTY_NOTICES.md", "app/__init__.py", "app/server.py", "app/security.py",
    "app/admin.py", "app/lan.py", "app/lan_setup.py", "app/integrations.py",
    "app/direct_tv.py", "app/network_identity.py", "app/router_ouis.py",
    "app/monitor.py", "app/ai.py", "web/index.html", "web/app.js", "web/styles.css",
    "web/icons.js", "scripts/setup-pi.sh", "scripts/package.py",
    "scripts/export-github.py", "docs/index.html", "docs/site.css", "docs/INSTALL.md",
    "docs/integrations.md", "docs/TV.md", "docs/operations.md", "docs/setup.html", "docs/guide.css", "docs/guide.js", "docs/repository.js", "docs/RELEASING.md", "docs/PUBLISH-CHECKLIST.md", *WORKFLOWS,
)

# Split local identifiers keep the policy itself free of private account text.
PRIVATE_IDENTIFIERS = ("john" + "scanlan", "scan" + "lanj", "co" + "dex")
UNSAFE_CONTENT = (
    re.compile(rb"/Users/[A-Za-z0-9_.-]+(?:/|$)"),
    re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}\b"),
)


class ExportError(ValueError):
    pass


def safe_source(root: Path, relative: str) -> Path:
    candidate = root / relative
    if not candidate.is_file() or candidate.is_symlink():
        raise ExportError(f"Required source file is missing or a symbolic link: {relative}")
    for parent in candidate.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ExportError(f"Symbolic-link parent is not exported: {relative}")
    if not candidate.resolve().is_relative_to(root):
        raise ExportError(f"Source path is outside the project: {relative}")
    return candidate


def selected_files(root: Path) -> dict[str, Path]:
    selected = {}
    for relative in (*ROOT_FILES, *SCRIPTS, *WORKFLOWS):
        if (root / relative).exists():
            selected[relative] = safe_source(root, relative)
    for destination, source in ROOT_REPLACEMENTS.items():
        selected[destination] = safe_source(root, source)
    for folder, suffixes in TREE_TYPES.items():
        directory = root / folder
        if not directory.is_dir() or directory.is_symlink():
            continue
        for candidate in sorted(directory.rglob("*")):
            relative = candidate.relative_to(root)
            if not candidate.is_file() or any(part in BLOCKED_PARTS or part.startswith(".") or "screenshot" in part.lower() for part in relative.parts):
                continue
            if candidate.suffix.lower() not in suffixes:
                continue
            selected[str(relative)] = safe_source(root, str(relative))
    nojekyll = root / "docs/.nojekyll"
    if nojekyll.is_file():
        selected["docs/.nojekyll"] = safe_source(root, "docs/.nojekyll")
    missing = [name for name in REQUIRED if name not in selected]
    if missing:
        raise ExportError("Public source is incomplete: " + ", ".join(missing))
    return dict(sorted(selected.items()))


def audit_content(relative: str, contents: bytes) -> None:
    lowered = contents.lower()
    if any(identifier.encode() in lowered for identifier in PRIVATE_IDENTIFIERS):
        raise ExportError(f"Private/local identifier found in {relative}; generalize the source before exporting.")
    if any(pattern.search(contents) for pattern in UNSAFE_CONTENT):
        raise ExportError(f"A local path or credential-like value was found in {relative}; review it before exporting.")
    if relative.lower().endswith(".svg") and re.search(rb"<(?:script|image|foreignObject)\b|\son[a-z]+\s*=", contents, re.I):
        raise ExportError(f"Active or embedded-raster SVG content found in {relative}; only inert vector artwork is exported.")


def export_tree(root: Path, destination: Path) -> dict:
    root, destination = root.resolve(), destination.expanduser().absolute()
    if destination.is_symlink():
        raise ExportError("The export destination cannot be a symbolic link.")
    destination = destination.parent.resolve() / destination.name
    if destination == root or root.is_relative_to(destination):
        raise ExportError("Choose a separate output directory, not the project or its parent.")
    if destination.is_relative_to(root) and destination.relative_to(root).parts[0] != "releases":
        raise ExportError("Inside the project, use the ignored releases directory for exports.")
    if destination.name in BLOCKED_PARTS - {"releases"}:
        raise ExportError("The output cannot be a runtime, secret, cache or repository-metadata directory.")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ExportError("The output already contains files. Choose a new or empty directory; existing work is never overwritten.")
    files = selected_files(root)
    payloads = {name: path.read_bytes() for name, path in files.items()}
    for relative, contents in payloads.items():
        audit_content(relative, contents)
    version_match = re.search(rb'__version__\s*=\s*["\']([^"\']+)["\']', payloads["app/__init__.py"])
    if not version_match:
        raise ExportError("Cannot determine the application's version.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": 1,
        "profile": "raspberry-pi-os-lite-64-docker",
        "version": version_match.group(1).decode(),
        "files": {name: hashlib.sha256(contents).hexdigest() for name, contents in payloads.items()},
    }
    with tempfile.TemporaryDirectory(prefix=".homebase-public-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        for relative, contents in payloads.items():
            output = stage / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(contents)
            output.chmod(0o755 if relative.endswith(".sh") or relative == "scripts/export-github.py" else 0o644)
        (stage / "EXPORT-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(stage, destination)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "releases/homebase-github")
    args = parser.parse_args(argv)
    try:
        manifest = export_tree(ROOT, args.output)
    except (ExportError, OSError) as exc:
        parser.error(str(exc))
    print(f"Public source exported: {args.output.expanduser().absolute()}")
    print(f"HomeBase OS v{manifest['version']} · {len(manifest['files'])} source files · Docker/Pi profile")
    print("Runtime state, accounts, private keys, native Mac launchers and personal screenshots are excluded.")
    print("Review before uploading. No remote repository, push, image publication or website deployment occurred.")


if __name__ == "__main__":
    main()
