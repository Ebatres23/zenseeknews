import unittest
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import app
from content_agent import classify, economic_impact_score, slugify, template_generate


class ContentAgentTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("AI & the Future!"), "ai-the-future")

    def test_classifier(self):
        self.assertEqual(classify("Stock market update"), "Money")

    def test_template_is_a_full_news_story_without_quiz(self):
        content = template_generate("Test topic", "Learning")
        self.assertGreaterEqual(len(content["story"]), 8)
        self.assertIn("economy_impact", content)
        self.assertIn("bottom_line", content)
        self.assertNotIn("quiz", content)

    def test_economic_impact_ranking(self):
        self.assertGreater(
            economic_impact_score("Federal Reserve interest rates", "Money"),
            economic_impact_score("Celebrity concert", "Entertainment"),
        )


class StoryRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.item = app.load_store()["items"][0]
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.ZenSeekHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_story_renders_without_generated_file(self):
        with tempfile.TemporaryDirectory() as root, patch.object(app, "ROOT", Path(root)):
            with urlopen(f"{self.base_url}/story/{self.item['slug']}") as response:
                page = response.read().decode("utf-8")
        self.assertIn(self.item["headline"], page)
        self.assertIn("Reputable source links", page)
        self.assertIn('href="/article.css"', page)
        self.assertIn(self.item["published"], page)

    def test_story_uses_saved_generated_content(self):
        item = dict(self.item, content={"headline": "Saved headline", "story": ["Saved story"]})
        with tempfile.TemporaryDirectory() as root, patch.object(app, "ROOT", Path(root)), \
                patch.object(app, "load_store", return_value={"items": [item]}):
            with urlopen(f"{self.base_url}/story/{item['slug']}") as response:
                page = response.read().decode("utf-8")
        self.assertIn("Saved story", page)

    def test_missing_story_returns_404(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/story/missing")
        self.assertEqual(error.exception.code, 404)

    def test_featured_guides_and_images_exist(self):
        class FeaturedLinks(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_card = False
                self.links = []
                self.images = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "a" and "orb-link" in attrs.get("class", "").split():
                    self.in_card = True
                    self.links.append(attrs["href"])
                elif tag == "img" and self.in_card:
                    self.images.append(attrs["src"])

            def handle_endtag(self, tag):
                if tag == "a":
                    self.in_card = False

        parser = FeaturedLinks()
        parser.feed((app.ROOT / "index.html").read_text(encoding="utf-8"))
        self.assertTrue(parser.links)
        for link in parser.links:
            with self.subTest(link=link):
                target = app.ROOT / link.lstrip("/")
                if link.endswith("/"):
                    target /= "index.html"
                with urlopen(f"{self.base_url}{link}") as response:
                    content = response.read()
                    self.assertEqual(content, target.read_bytes())
                    self.assertIn(b'href="/guide-theme.css"', content)
        for image in parser.images:
            with self.subTest(image=image):
                self.assertTrue((app.ROOT / image.lstrip("/")).is_file())


if __name__ == "__main__":
    unittest.main()
