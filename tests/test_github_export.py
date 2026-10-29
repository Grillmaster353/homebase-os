"""Public export privacy and completeness checks using temporary source fixtures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export-github.py"
spec = importlib.util.spec_from_file_location("github_export", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class GitHubExportTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory) / "source"
        for name in {*exporter.REQUIRED, *exporter.ROOT_REPLACEMENTS.values()}:
            # Root public documents are supplied by the explicit profile mapping.
            if name in exporter.ROOT_REPLACEMENTS:
                continue
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (root / "app/__init__.py").write_text('__version__ = "1.0.0"\n')
        (root / "docs/public/README.md").write_text("# HomeBase OS\nDocker on Raspberry Pi OS Lite 64-bit.\n")
        (root / "docs/public/START-HERE.md").write_text("# Terminal setup\nUse your own account.\n")
        return root, Path(directory) / "public-output"

    def test_profile_is_complete_and_omits_private_state_and_native_launchers(self):
        with tempfile.TemporaryDirectory() as directory:
            root, destination = self.fixture(directory)
            for name in (".data/master.key", ".data/vault.enc", "certs/server.key", ".setup-ca/rootCA.key",
                         "workspace/personal.md", ".git/config", "web/screenshots/personal.svg",
                         "web/personal-screenshot.png", "web/personal-screenshot.svg", "scripts/start.sh", "scripts/start-lan.sh"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private fixture must not be exported")
            logo = root / "web/assets/brands/example.svg"
            logo.parent.mkdir(parents=True, exist_ok=True)
            logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0h1v1z"/></svg>')
            manifest = exporter.export_tree(root, destination)
            self.assertEqual(manifest["version"], "1.0.0")
            self.assertEqual((destination / "README.md").read_text(), (root / "docs/public/README.md").read_text())
            self.assertTrue((destination / ".github/workflows/ci.yml").is_file())
            self.assertTrue((destination / "requirements-tv.txt").is_file())
            self.assertTrue((destination / "web/assets/brands/example.svg").is_file())
            for name in (".data", "certs", ".setup-ca", "workspace", ".git", "web/screenshots", "web/personal-screenshot.png", "web/personal-screenshot.svg", "scripts/start.sh", "scripts/start-lan.sh"):
                self.assertFalse((destination / name).exists(), name)
            saved = json.loads((destination / "EXPORT-MANIFEST.json").read_text())
            self.assertEqual(saved, manifest)
            self.assertNotIn(str(root), json.dumps(saved))

    def test_private_identifiers_and_paths_fail_before_output_is_created(self):
        private_path = "/Us" + "ers/" + "fixture-person/" + "private"
        for private_value in (exporter.PRIVATE_IDENTIFIERS[0], private_path):
            with self.subTest(value="private fixture"), tempfile.TemporaryDirectory() as directory:
                root, destination = self.fixture(directory)
                (root / "app/server.py").write_text(private_value)
                with self.assertRaises(exporter.ExportError):
                    exporter.export_tree(root, destination)
                self.assertFalse(destination.exists())

    def test_private_key_material_in_an_allowed_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root, destination = self.fixture(directory)
            material = b"-----BEGIN " + b"PRIVATE KEY-----\nfixture\n"
            (root / "web/app.js").write_bytes(material)
            with self.assertRaises(exporter.ExportError):
                exporter.export_tree(root, destination)
            self.assertFalse(destination.exists())

    def test_missing_runtime_dependency_and_symlink_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, destination = self.fixture(directory)
            (root / "requirements-tv.txt").unlink()
            with self.assertRaises(exporter.ExportError):
                exporter.export_tree(root, destination)
        with tempfile.TemporaryDirectory() as directory:
            root, destination = self.fixture(directory)
            private = Path(directory) / "outside.py"
            private.write_text("not source")
            (root / "app/linked.py").symlink_to(private)
            with self.assertRaises(exporter.ExportError):
                exporter.export_tree(root, destination)

    def test_existing_work_is_never_overwritten_and_source_is_not_an_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root, destination = self.fixture(directory)
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("preserve")
            with self.assertRaises(exporter.ExportError):
                exporter.export_tree(root, destination)
            self.assertEqual(sentinel.read_text(), "preserve")
            with self.assertRaises(exporter.ExportError):
                exporter.export_tree(root, root)
            with self.assertRaises(exporter.ExportError):
                exporter.export_tree(root, root / "web/output")


if __name__ == "__main__":
    unittest.main()
