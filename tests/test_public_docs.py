"""Public documentation links and publishing-boundary regression checks."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if values.get("href"):
            self.links.append(values["href"])
        if tag == "script":
            self.scripts.append(values.get("src", "inline"))
        if tag == "link" and values.get("rel") == "stylesheet":
            self.styles.append(values.get("href", ""))


class PublicDocumentationTests(unittest.TestCase):
    def test_public_markdown_links_resolve_after_profile_rebasing(self):
        documents = {
            "docs/public/README.md": "README.md",
            "docs/public/START-HERE.md": "START-HERE.md",
            "docs/INSTALL.md": "docs/INSTALL.md",
            "docs/integrations.md": "docs/integrations.md",
            "docs/operations.md": "docs/operations.md",
            "docs/TV.md": "docs/TV.md",
            "docs/RELEASING.md": "docs/RELEASING.md",
            "docs/PUBLISH-CHECKLIST.md": "docs/PUBLISH-CHECKLIST.md",
            "SECURITY.md": "SECURITY.md",
            "CONTRIBUTING.md": "CONTRIBUTING.md",
            "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
        }
        for source, exported in documents.items():
            text = (ROOT / source).read_text()
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                parsed = urlsplit(link)
                if parsed.scheme or not parsed.path:
                    continue
                target = ((ROOT / exported).parent / unquote(parsed.path)).resolve()
                with self.subTest(document=source, link=link):
                    self.assertTrue(target.is_relative_to(ROOT), "Link must stay in the exported project")
                    self.assertTrue(target.exists(), f"Missing documentation target: {link}")

    def test_static_docs_have_local_assets_and_resolving_navigation(self):
        parser = Links()
        html = (ROOT / "docs/index.html").read_text()
        parser.feed(html)
        self.assertIn("1.0.0", html)
        self.assertEqual(parser.scripts, ["repository.js"])
        self.assertTrue((ROOT / "docs/repository.js").is_file())
        self.assertEqual(parser.styles, ["site.css"])
        for link in parser.links:
            parsed = urlsplit(link)
            if parsed.scheme:
                continue
            if parsed.path:
                self.assertTrue((ROOT / "docs" / unquote(parsed.path)).is_file())
            elif parsed.fragment:
                self.assertIn(parsed.fragment, parser.ids)

    def test_pages_are_manual_and_ci_never_pushes_images(self):
        pages = (ROOT / ".github/workflows/pages.yml").read_text()
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("workflow_dispatch:", pages)
        self.assertNotRegex(pages, r"(?m)^\s+(?:push|pull_request|schedule):")
        self.assertIn("path: docs", pages)
        self.assertIn("push: false", ci)
        self.assertNotIn("push: true", ci)
        for workflow in (pages, ci):
            for reference in re.findall(r"uses:\s*([^\s#]+)", workflow):
                self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")

    def test_complete_setup_guide_links_and_copy_controls(self):
        parser = Links()
        html = (ROOT / "docs/setup.html").read_text()
        parser.feed(html)
        self.assertEqual(parser.scripts, ["guide.js"])
        self.assertEqual(parser.styles, ["site.css", "guide.css"])
        self.assertIn("sudo bash scripts/setup-pi.sh", html)
        self.assertIn("Grillmaster353/homebase-os.git", html)
        for link in parser.links:
            parsed = urlsplit(link)
            if parsed.scheme:
                continue
            if parsed.path:
                self.assertTrue((ROOT / "docs" / unquote(parsed.path)).is_file(), link)
            elif parsed.fragment:
                self.assertIn(unquote(parsed.fragment), parser.ids)


if __name__ == "__main__":
    unittest.main()
