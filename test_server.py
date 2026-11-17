import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server


FEED = b"""<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>News &amp; more</title><link>https://example.com/story</link>
<description>&lt;p&gt;An extended description.&lt;/p&gt;</description>
<pubDate>Tue, 17 Nov 2026 12:00:00 GMT</pubDate>
<media:thumbnail url="https://example.com/image.jpg"/></item>
<item><title>Unsafe</title><link>javascript:alert(1)</link>
<pubDate>Tue, 17 Nov 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.data_file = patch.object(server, "DATA_FILE", Path(self.directory.name) / "news.json")
        self.data_file.start()
        server.state.clear()
        server.state.update({"articles": [], "last_updated": None, "views": {}})

    def tearDown(self):
        self.data_file.stop()
        self.directory.cleanup()

    def test_parse_feed(self):
        articles = server.parse_feed(FEED, "Example")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "News & more")
        self.assertEqual(articles[0]["description"], "An extended description.")
        self.assertEqual(articles[0]["image"], "https://example.com/image.jpg")
        self.assertEqual(articles[0]["published_at"], "2026-11-17T12:00:00+00:00")

    def test_refresh_retains_unavailable_sources_and_cache(self):
        old = server.parse_feed(FEED, "Unavailable")[0]
        old["id"] = "old"
        server.state["articles"] = [old]
        with patch.object(server, "SOURCES", {"Example": "one", "Unavailable": "two"}):
            with patch.object(server, "fetch_source", side_effect=lambda name, url: (
                server.parse_feed(FEED, name) if name == "Example" else []
            )):
                server.refresh()
        self.assertEqual({a["source"] for a in server.state["articles"]}, {"Example", "Unavailable"})
        self.assertIsNotNone(server.state["last_updated"])
        self.assertEqual(len(json.loads(server.DATA_FILE.read_text())["articles"]), 2)

    def test_api_records_views_and_rejects_unknown_articles(self):
        article = server.parse_feed(FEED, "Example")[0]
        server.state["articles"] = [article]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", httpd.server_port)
            connection.request("POST", f"/api/articles/{article['id']}/view")
            response = connection.getresponse()
            self.assertEqual(response.status, 204)
            response.read()
            connection.request("GET", "/api/news")
            response = connection.getresponse()
            self.assertEqual(json.loads(response.read())["articles"][0]["views"], 1)
            connection.request("POST", "/api/articles/" + "0" * 24 + "/view")
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()
        finally:
            httpd.shutdown()
            thread.join()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
