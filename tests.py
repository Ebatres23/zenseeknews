import unittest
import tempfile
import threading
import json
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import app
import content_agent
import newsletter
from content_agent import classify, economic_impact_score, slugify, template_generate


class NewsletterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 18, 15, 5, tzinfo=timezone.utc)
        self.env = {
            "ENABLE_NEWSLETTER": "1", "DATABASE_URL": "postgres://test",
            "BREVO_API_KEY": "test-key", "BREVO_LIST_ID": "7",
            "BREVO_SENDER_EMAIL": "news@example.com",
            "BREVO_SIGNUP_URL": "https://abc.sibforms.com/serve/form",
            "NEWSLETTER_POSTAL_ADDRESS": "PO Box 1",
        }
        self.story = {
            "slug": "daily-today", "headline": "Today <news>", "description": "A useful summary.",
            "content": {"story": ["Summary."]}, "published_at": self.now.isoformat(),
        }
        self.recent = {
            "slug": "daily-recent", "headline": "A developing story",
            "content": {"what_to_watch": ["Officials will publish a report."]},
            "published_at": (self.now - timedelta(days=2)).isoformat(),
        }

    def test_digest_includes_grounded_recent_updates_and_unsubscribe(self):
        old = dict(self.recent, slug="old", headline="Old story",
                   published_at=(self.now - timedelta(days=8)).isoformat())
        unsupported = dict(self.recent, slug="unsupported", content={"what_to_watch": []})
        page = newsletter.digest_html([self.story], [old, unsupported, self.recent], self.now, "PO <Box>")
        self.assertIn("Today &lt;news&gt;", page)
        self.assertIn("Officials will publish a report.", page)
        self.assertNotIn("Old story", page)
        self.assertNotIn("unsupported", page)
        self.assertIn('href="{{ unsubscribe }}"', page)
        self.assertIn("PO &lt;Box&gt;", page)

    def test_newsletter_sends_once_and_tracks_brevo_campaign(self):
        with patch.dict("os.environ", self.env, clear=True), \
                patch("news_storage.newsletter_attempted", side_effect=[False, True]), \
                patch("news_storage.load_feed", return_value=({"updated_at": self.now.isoformat(), "items": [self.story]}, {})), \
                patch("news_storage.recent_stories", return_value=[self.recent]), \
                patch("news_storage.claim_newsletter", return_value=True) as claim, \
                patch("news_storage.update_newsletter") as update, \
                patch.object(newsletter, "brevo_request", side_effect=[{"id": 123}, {}]) as request:
            self.assertEqual(newsletter.run_daily(self.now), 123)
            self.assertIsNone(newsletter.run_daily(self.now))
            claim.assert_called_once_with(self.now.date())
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.args[0], "/emailCampaigns/123/sendNow")
            self.assertEqual(request.call_args_list[0].args[2]["recipients"]["listIds"], [7])
            self.assertIn("Officials will publish a report.", request.call_args_list[0].args[2]["htmlContent"])
            self.assertEqual(update.call_args_list[0].args, (self.now.date(), "sending", 123))
            self.assertEqual(update.call_args_list[1].args, (self.now.date(), "submitted", 123))

    def test_stale_feed_never_claims_or_sends(self):
        stale = (self.now - timedelta(hours=49)).isoformat()
        with patch.dict("os.environ", self.env, clear=True), \
                patch("news_storage.newsletter_attempted", return_value=False), \
                patch("news_storage.load_feed", return_value=({"updated_at": stale, "items": [self.story]}, {})), \
                patch("news_storage.claim_newsletter") as claim, patch.object(newsletter, "brevo_request") as request:
            with self.assertRaisesRegex(RuntimeError, "stale"):
                newsletter.run_daily(self.now)
            claim.assert_not_called()
            request.assert_not_called()

    def test_ambiguous_delivery_failure_is_recorded_without_retry(self):
        with patch.dict("os.environ", self.env, clear=True), \
                patch("news_storage.newsletter_attempted", return_value=False), \
                patch("news_storage.load_feed", return_value=({"updated_at": self.now.isoformat(), "items": [self.story]}, {})), \
                patch("news_storage.recent_stories", return_value=[]), \
                patch("news_storage.claim_newsletter", return_value=True), \
                patch("news_storage.update_newsletter") as update, \
                patch.object(newsletter, "brevo_request", side_effect=[{"id": 123}, TimeoutError("timeout")]) as request:
            with self.assertRaisesRegex(RuntimeError, "check Brevo"):
                newsletter.run_daily(self.now)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(update.call_args.args, (self.now.date(), "failed", 123))

    def test_configuration_rejects_non_brevo_signup_url(self):
        with patch.dict("os.environ", {**self.env, "BREVO_SIGNUP_URL": "https://sibforms.com.attacker.example/form"}, clear=True):
            with self.assertRaisesRegex(ValueError, "signup form URL"):
                newsletter.configuration()

    def test_brevo_request_posts_campaign_to_provider(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return b'{"id":123}'
        with patch("urllib.request.urlopen", return_value=Response()) as urlopen_mock:
            self.assertEqual(newsletter.brevo_request("/emailCampaigns", "key", {"name": "Digest"}), {"id": 123})
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/emailCampaigns")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Api-key"), "key")
        self.assertEqual(json.loads(request.data), {"name": "Digest"})


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

    def test_publisher_feed_summaries_and_related_titles(self):
        body = b'''<rss><channel><item>
            <title>Power failure delays trains in Chicago</title>
            <link>https://publisher.example/train-story</link>
            <pubDate>Tue, 17 Nov 2026 10:00:00 GMT</pubDate>
            <description><![CDATA[<p>A power outage caused train delays.</p>
            <p>Crews restored service after two hours.</p>]]></description>
        </item></channel></rss>'''
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, size):
                return body
        class Opener:
            def open(self, request, timeout):
                return Response()
        with patch.object(content_agent, "PUBLISHER_FEEDS", (("Publisher", "https://publisher.example/feed"),)), \
                patch.object(content_agent, "DISCOVERY_FEEDS", ()), \
                patch("urllib.request.build_opener", return_value=Opener()):
            articles = content_agent.fetch_publisher_news()
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["description"], "A power outage caused train delays. Crews restored service after two hours.")
        self.assertTrue(content_agent.related_title("Power failure delays Chicago trains", articles[0]["title"]))
        self.assertFalse(content_agent.related_title("Power outage closes hospitals in Boston", articles[0]["title"]))

    def test_reporting_detail_rejects_repeated_headlines(self):
        headline = {"title": "Power failure delays Chicago trains",
                    "description": "Power failure delays Chicago trains Example News"}
        self.assertFalse(content_agent.has_reporting_detail([headline]))
        headline["description"] = (
            "The transit operator said crews repaired damaged equipment overnight. "
            "Service resumed after two hours, though passengers faced continued delays."
        )
        self.assertTrue(content_agent.has_reporting_detail([headline]))

    def test_discovery_search_only_accepts_requested_publisher(self):
        body = b'''<rss><channel>
            <item><title>AP story</title><link>https://news.google.com/ap</link>
                <source url="https://apnews.com">The Associated Press</source></item>
            <item><title>Other story</title><link>https://news.google.com/other</link>
                <source url="https://other.example">Other News</source></item>
        </channel></rss>'''
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, size):
                return body
        class Opener:
            def open(self, request, timeout):
                return Response()
        with patch.object(content_agent, "PUBLISHER_FEEDS", ()), \
                patch.object(content_agent, "DISCOVERY_FEEDS", (("Associated Press", "https://news.google.com/feed", "apnews.com"),)), \
                patch("urllib.request.build_opener", return_value=Opener()):
            articles = content_agent.fetch_publisher_news()
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source"], "The Associated Press")
        self.assertEqual(articles[0]["publisher_group"], "Associated Press")

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
                    patch.object(content_agent, "fetch_major_news", return_value=[]), \
                    patch.object(content_agent, "fetch_publisher_news", return_value=[]):
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
            "lede": "Trains were delayed.", "story": [
                "The transit operator reported a power failure that delayed trains across Chicago, "
                "leaving passengers waiting while crews inspected the affected equipment.",
                "Crews restored service after two hours, the operator said, though passengers "
                "continued to face delays while trains returned to their normal schedules."
            ],
            "notable_details": []
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
        self.assertIn("notable_details", prompt)
        self.assertIn("2-5", prompt)
        self.assertEqual(result["subheadline"], "An outage delayed trains.")

    def test_model_rejects_thin_story(self):
        response_body = {"choices": [{"message": {"content": json.dumps({
            "headline": "Transit delays", "subheadline": "An outage delayed trains.",
            "lede": "Trains were delayed.", "story": ["Read the linked reports for details."],
            "notable_details": []
        })}}]}
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps(response_body).encode()
        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaisesRegex(content_agent.InsufficientBriefingError, "thin"):
                content_agent.llm_generate("Train delays", "News", [], api_key="test")

    def test_notable_details_render_only_when_supplied(self):
        item = app.load_store()["items"][0]
        content = {"headline": "Example", "story": ["A summary."],
                   "notable_details": ["Crews restored service in <two> hours."]}
        page = content_agent.render_page(item["topic"], item["category"], content, item["published"],
                                         1, "", item["sources"], item["fact_check"], 0)
        self.assertIn("Interesting details", page)
        self.assertIn("Crews restored service in &lt;two&gt; hours.", page)
        content["notable_details"] = []
        page = content_agent.render_page(item["topic"], item["category"], content, item["published"],
                                         1, "", item["sources"], item["fact_check"], 0)
        self.assertNotIn("Interesting details", page)

    def test_database_refresh_saves_source_image_and_model_summary(self):
        headline = {
            "title": "Power outage affects trains - Example News",
            "source": "Example News",
            "url": "https://example.com/report",
            "published": "Tue, 17 Nov 2026 10:00:00 GMT",
            "description": "A power failure delayed trains across the city as repair crews worked overnight to restore service for commuters.",
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
                    patch.object(content_agent, "fetch_publisher_news", return_value=[]), \
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

    def test_refresh_includes_matching_publisher_context_without_unrelated_articles(self):
        headline = {"title": "Power failure delays Chicago trains - News", "source": "News",
                    "url": "https://news.example/original", "published": "",
                    "description": "A failure delayed trains.", "leaning": "Not yet classified"}
        related = {"title": "Power failure delays trains in Chicago", "source": "Publisher",
                   "url": "https://publisher.example/match", "published": "",
                   "description": "Crews restored service after two hours, while the operator warned passengers of continued delays across the city.", "leaning": "Not yet classified"}
        unrelated = dict(related, title="Power outage closes hospitals in Boston",
                         url="https://publisher.example/other")
        briefing = {"headline": "Train delays", "subheadline": "Trains were delayed.",
                    "lede": "A power failure delayed service.", "story": ["Crews restored service."]}
        with tempfile.TemporaryDirectory() as root:
            catalog = Path(root) / "catalog.json"
            catalog.write_text('{"items": []}')
            with patch.dict("os.environ", {"DATABASE_URL": "postgres://test", "OPENAI_API_KEY": "test"}, clear=True), \
                    patch.object(content_agent, "CATALOG_PATH", catalog), \
                    patch.object(content_agent, "fetch_major_news", return_value=[headline]), \
                    patch.object(content_agent, "fetch_publisher_news", return_value=[related, unrelated]), \
                    patch.object(content_agent, "source_collector_agent", return_value=[]), \
                    patch.object(content_agent, "llm_generate", return_value=briefing) as model, \
                    patch("news_storage.load_feed", return_value=None), patch("news_storage.save_feed"):
                store = content_agent.run(1, database_conn=object(), refresh_started="start")
            self.assertEqual([a["url"] for a in store["items"][0]["sources"]],
                             [headline["url"], related["url"]])
            self.assertIn("two hours", model.call_args.args[2][1]["description"])

    def test_refresh_includes_ap_and_local_stories_ahead_of_full_national_feed(self):
        def headline(title, group=""):
            return {"title": title, "source": group or "National News",
                    "url": "https://example.com/" + slugify(title), "published": "",
                    "description": "Officials announced the new development on Tuesday after months of public meetings and discussions with local residents.",
                    "leaning": "Not yet classified",
                    "publisher_group": group}

        national = [headline(f"National story number {i}") for i in range(20)]
        discovered = [headline("AP reports a national development", "Associated Press"),
                      headline("Reno road project approved", "KOLO 8 News Now")]
        briefing = {"headline": "News update", "subheadline": "A report.",
                    "lede": "A report.", "story": ["A report."]}
        with tempfile.TemporaryDirectory() as root:
            catalog = Path(root) / "catalog.json"
            catalog.write_text('{"items": []}')
            with patch.dict("os.environ", {"DATABASE_URL": "postgres://test", "OPENAI_API_KEY": "test"}, clear=True), \
                    patch.object(content_agent, "CATALOG_PATH", catalog), \
                    patch.object(content_agent, "fetch_major_news", return_value=national), \
                    patch.object(content_agent, "fetch_publisher_news", return_value=discovered), \
                    patch.object(content_agent, "source_collector_agent", return_value=[]), \
                    patch.object(content_agent, "llm_generate", return_value=briefing), \
                    patch("news_storage.load_feed", return_value=None), patch("news_storage.save_feed"):
                store = content_agent.run(8, database_conn=object(), refresh_started="start")
            topics = {item["topic"] for item in store["items"]}
            self.assertIn(discovered[0]["title"], topics)
            self.assertIn(discovered[1]["title"], topics)
            self.assertEqual(len(store["items"]), 8)

    def test_refresh_can_use_publisher_stories_when_national_feed_fails(self):
        local = {"title": "Reno road project approved", "source": "KOLO 8 News Now",
                 "url": "https://www.kolotv.com/story", "published": "",
                 "description": "Officials approved the road project on Tuesday after months of public meetings and discussions with local residents.",
                 "leaning": "Not yet classified", "publisher_group": "KOLO 8 News Now"}
        briefing = {"headline": "Road project", "subheadline": "A project was approved.",
                    "lede": "The project was approved.", "story": ["The project was approved."]}
        with tempfile.TemporaryDirectory() as root:
            catalog = Path(root) / "catalog.json"
            catalog.write_text('{"items": []}')
            with patch.dict("os.environ", {"DATABASE_URL": "postgres://test", "OPENAI_API_KEY": "test"}, clear=True), \
                    patch.object(content_agent, "CATALOG_PATH", catalog), \
                    patch.object(content_agent, "fetch_major_news", return_value=[]), \
                    patch.object(content_agent, "fetch_publisher_news", return_value=[local]), \
                    patch.object(content_agent, "source_collector_agent", return_value=[]), \
                    patch.object(content_agent, "llm_generate", return_value=briefing), \
                    patch("news_storage.load_feed", return_value=None), patch("news_storage.save_feed"):
                store = content_agent.run(1, database_conn=object(), refresh_started="start")
            self.assertEqual(store["items"][0]["sources"][0]["source"], "KOLO 8 News Now")

    def test_refresh_skips_thin_candidates_and_keeps_later_detailed_story(self):
        def headline(title, description):
            return {"title": title, "source": "Publisher", "url": "https://example.com/" + slugify(title),
                    "published": "", "description": description, "leaning": "Not yet classified"}
        headlines = [
            headline("First story", "First story Publisher"),
            headline("Second story", "Officials announced the new development on Tuesday after months of public meetings and discussions with local residents."),
            headline("Third story", "The transit operator reported damage to equipment overnight and crews restored service after two hours of repairs."),
        ]
        briefing = {"headline": "Third story", "subheadline": "Service resumed.",
                    "lede": "Service resumed.", "story": ["Details."]}
        with tempfile.TemporaryDirectory() as root:
            catalog = Path(root) / "catalog.json"
            catalog.write_text('{"items": []}')
            with patch.dict("os.environ", {"DATABASE_URL": "postgres://test", "OPENAI_API_KEY": "test"}, clear=True), \
                    patch.object(content_agent, "CATALOG_PATH", catalog), \
                    patch.object(content_agent, "fetch_major_news", return_value=headlines), \
                    patch.object(content_agent, "fetch_publisher_news", return_value=[]), \
                    patch.object(content_agent, "source_collector_agent", return_value=[]), \
                    patch.object(content_agent, "llm_generate", side_effect=[content_agent.InsufficientBriefingError("thin"), briefing]) as model, \
                    patch("news_storage.load_feed", return_value=None), patch("news_storage.save_feed"):
                store = content_agent.run(1, database_conn=object(), refresh_started="start")
            self.assertEqual(store["items"][0]["topic"], "Third story")
            self.assertEqual(model.call_count, 2)

    def test_refresh_keeps_previous_feed_when_no_sources_have_detail(self):
        headline = {"title": "Power failure delays trains", "source": "News",
                    "url": "https://example.com/story", "published": "",
                    "description": "Power failure delays trains News", "leaning": "Not yet classified"}
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://test", "OPENAI_API_KEY": "test"}, clear=True), \
                patch.object(content_agent, "fetch_major_news", return_value=[headline]), \
                patch.object(content_agent, "fetch_publisher_news", return_value=[]), \
                patch.object(content_agent, "source_collector_agent", return_value=[]), \
                patch.object(content_agent, "llm_generate") as model, \
                patch("news_storage.save_feed") as save:
            with self.assertRaisesRegex(RuntimeError, "No sufficiently detailed"):
                content_agent.run(1, database_conn=object(), refresh_started="start")
            model.assert_not_called()
            save.assert_not_called()


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

    def test_archived_story_does_not_show_generic_legacy_filler(self):
        with tempfile.TemporaryDirectory() as root, patch.object(app, "ROOT", Path(root)):
            with urlopen(f"{self.base_url}/story/{self.item['slug']}") as response:
                page = response.read().decode("utf-8")
        self.assertIn("A full briefing is not available for this archived story.", page)
        self.assertNotIn("Economic impact depends on what happens next", page)
        self.assertNotIn("Read the source links for the full context", page)
        self.assertNotIn("Moderate to high", page)
        self.assertNotIn("U.S. economic impact ranking", page)

    def test_newsletter_availability_does_not_expose_provider_key(self):
        env = {
            "ENABLE_NEWSLETTER": "1", "DATABASE_URL": "postgres://test",
            "BREVO_API_KEY": "secret", "BREVO_LIST_ID": "7",
            "BREVO_SENDER_EMAIL": "news@example.com",
            "BREVO_SIGNUP_URL": "https://abc.sibforms.com/serve/form",
            "NEWSLETTER_POSTAL_ADDRESS": "PO Box 1",
        }
        with patch.dict("os.environ", env, clear=True):
            with urlopen(f"{self.base_url}/api/v1/newsletter") as response:
                body = json.load(response)
        self.assertEqual(body, {"signup_url": env["BREVO_SIGNUP_URL"]})
        with patch.dict("os.environ", {}, clear=True):
            with urlopen(f"{self.base_url}/api/v1/newsletter") as response:
                self.assertEqual(json.load(response), {"signup_url": None})

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
