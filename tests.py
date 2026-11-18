import unittest
import tempfile
import threading
import json
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import app
import content_agent
from content_agent import classify, economic_impact_score, slugify, template_generate


class ContentAgentTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("AI & the Future!"), "ai-the-future")

    def test_classifier(self):
        self.assertEqual(classify("Stock market update"), "Money")

    def test_archived_fallback_does_not_invent_a_story(self):
        content = template_generate("Test topic", "Learning")
        self.assertIn("No source reports", content["story"][0])
        self.assertEqual(content["why_it_matters"], [])
        self.assertNotIn("quiz", content)

    def test_economic_impact_ranking(self):
        self.assertGreater(
            economic_impact_score("Federal Reserve interest rates", "Money"),
            economic_impact_score("Celebrity concert", "Entertainment"),
        )

    def test_feed_images_use_source_media_only(self):
        item = ET.fromstring('''<item xmlns:media="http://search.yahoo.com/mrss/">
            <enclosure url="https://example.com/audio.mp3" type="audio/mpeg"/>
            <media:thumbnail url="https://example.com/news.jpg"/>
        </item>''')
        self.assertEqual(content_agent.feed_image(item), "https://example.com/news.jpg")
        self.assertIsNone(content_agent.feed_image(ET.fromstring(
            '<item><enclosure url="data:image/svg+xml,test" type="image/svg+xml"/></item>'
        )))

    def test_local_refresh_waits_half_an_hour_and_retries_failure(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(content_agent, "_last_local_refresh", 0.0), \
                patch.object(content_agent, "run", side_effect=[{"items": [1]}, RuntimeError("offline"), {"items": [2]}]) as run, \
                patch.object(content_agent.time, "monotonic", side_effect=[100, 1000, 1900, 1900, 1901, 1901]):
            self.assertEqual(content_agent.run_scheduled()["items"], [1])
            self.assertIsNone(content_agent.run_scheduled())
            with self.assertRaises(RuntimeError):
                content_agent.run_scheduled()
            self.assertEqual(content_agent.run_scheduled()["items"], [2])
            self.assertEqual(run.call_count, 3)

    def test_database_lease_coordinates_and_manual_run_respects_busy(self):
        @contextmanager
        def lease(force=False):
            yield False
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://test"}, clear=True), \
                patch("news_storage.refresh_lease", lease), patch.object(content_agent, "run") as run:
            self.assertIsNone(content_agent.run_scheduled(force=True))
            run.assert_not_called()

    def test_database_refresh_holds_lease_through_publish(self):
        conn = object()
        @contextmanager
        def lease(force=False):
            yield conn, "start"
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://test"}, clear=True), \
                patch("news_storage.refresh_lease", lease), patch.object(content_agent, "run", return_value={"items": [1]}) as run:
            self.assertEqual(content_agent.run_scheduled(3), {"items": [1]})
            run.assert_called_once_with(3, database_conn=conn, refresh_started="start")

    def test_failed_or_unconfigured_refresh_never_overwrites_feed(self):
        with tempfile.TemporaryDirectory() as root:
            store = Path(root) / "daily-content.json"
            store.write_text('{"items": ["previous"]}')
            with patch.object(content_agent, "STORE_PATH", store), patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                    content_agent.run()
            with patch.object(content_agent, "STORE_PATH", store), \
                    patch.dict("os.environ", {"OPENAI_API_KEY": "test"}, clear=True), \
                    patch.object(content_agent, "fetch_major_news", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "No news"):
                    content_agent.run()
            self.assertEqual(json.loads(store.read_text()), {"items": ["previous"]})

    def test_model_rejects_missing_story(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps({"choices": [{"message": {"content": json.dumps({
                    "headline": "Example", "subheadline": "Summary", "lede": "Context", "story": []
                })}}]}).encode()
        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                content_agent.llm_generate("Example", "News", [], api_key="test")

    def test_model_receives_source_context_for_original_briefing(self):
        response_body = {"choices": [{"message": {"content": json.dumps({
            "headline": "Transit delays", "subheadline": "An outage delayed trains.",
            "lede": "Trains were delayed.", "story": ["Officials reported an outage."]
        })}}]}
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps(response_body).encode()
        source = {"title": "Train delays", "source": "Example News", "description": "Power outage reported"}
        with patch("urllib.request.urlopen", return_value=Response()) as urlopen_mock:
            result = content_agent.llm_generate("Train delays", "News", [source], api_key="test")
        request = urlopen_mock.call_args.args[0]
        prompt = json.loads(request.data)["messages"][1]["content"]
        self.assertIn("Power outage reported", prompt)
        self.assertIn("Never reproduce", prompt)
        self.assertEqual(result["subheadline"], "An outage delayed trains.")

    def test_database_refresh_saves_source_image_and_model_summary(self):
        headline = {
            "title": "Power outage affects trains - Example News",
            "source": "Example News",
            "url": "https://example.com/report",
            "published": "Tue, 17 Nov 2026 10:00:00 GMT",
            "description": "A power failure delayed trains.",
            "leaning": "Not yet classified",
            "image": "https://example.com/report.jpg",
        }
        briefing = {
            "headline": "Power Failure Delays Trains",
            "subheadline": "A power outage delayed trains, the operator said.",
            "lede": "The operator reported a power failure.",
            "story": ["Example News reported delays after the power failure."],
        }
        with tempfile.TemporaryDirectory() as root:
            catalog = Path(root) / "catalog.json"
            catalog.write_text('{"items": []}')
            conn = object()
            with patch.dict("os.environ", {"DATABASE_URL": "postgres://test", "OPENAI_API_KEY": "test"}, clear=True), \
                    patch.object(content_agent, "CATALOG_PATH", catalog), \
                    patch.object(content_agent, "fetch_major_news", return_value=[headline]), \
                    patch.object(content_agent, "source_collector_agent", return_value=[]), \
                    patch.object(content_agent, "llm_generate", return_value=briefing), \
                    patch("news_storage.load_feed", return_value=None), \
                    patch("news_storage.save_feed") as save:
                store = content_agent.run(1, database_conn=conn, refresh_started="2026-11-18T00:00:00Z")
            item = store["items"][0]
            self.assertEqual(item["description"], briefing["subheadline"])
            self.assertEqual(item["image"], headline["image"])
            self.assertEqual(item["content"]["story"], briefing["story"])
            save.assert_called_once()
            self.assertIs(save.call_args.args[0], conn)
            self.assertEqual(save.call_args.args[1], store)


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
