import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
spec = importlib.util.spec_from_file_location("package_source", SCRIPT)
package_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_source)


class PackageTests(unittest.TestCase):
    def test_bundle_includes_source_and_excludes_credentials_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("START-HERE.md", "app/server.py", "web/index.html", ".data/master.key", ".setup-ca/rootCA.key", "certs/server.key", "homebase-rootCA.crt", "app/.env", "app/key.pem", "app/__pycache__/cache.py", "workspace/private.md"):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("test", encoding="utf-8")
            (root / "app" / "linked.py").symlink_to(root / ".data" / "master.key")
            archive_path = root / "out.tar.gz"
            package_source.build_archive(root, archive_path)
            with tarfile.open(archive_path) as archive:
                names = archive.getnames()
            self.assertEqual(names, ["home-command/START-HERE.md", "home-command/app/server.py", "home-command/web/index.html"])
            self.assertTrue(archive_path.with_suffix(".gz.sha256").is_file())


if __name__ == "__main__":
    unittest.main()
