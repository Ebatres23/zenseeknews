#!/usr/bin/env python3
"""ZenSeek's small HTTP server and RSS content updater."""

import hashlib
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "news.json"
SOURCES = {
    "BBC News": "https://feeds.bbci.co.uk/news/rss.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "NPR": "https://feeds.npr.org/1001/rss.xml",
}
MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
MAX_FEED_BYTES = 2 * 1024 * 1024
lock = threading.Lock()
state = {"articles": [], "last_updated": None, "views": {}}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    parser = TextExtractor()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def web_url(value):
    value = (value or "").strip()
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def published_date(value):
    try:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def parse_feed(content, source):
    root = ET.fromstring(content)
    articles = []
    for item in root.findall(".//item"):
        url = web_url(item.findtext("link"))
        title = plain_text(item.findtext("title"))
        published = published_date(item.findtext("pubDate"))
        if not (url and title and published):
            continue
        description = plain_text(item.findtext("description") or item.findtext(CONTENT + "encoded"))
        image = ""
        for element in item.iter():
            if element.tag in (MEDIA + "content", MEDIA + "thumbnail"):
                image = web_url(element.get("url"))
            elif element.tag == "enclosure" and element.get("type", "").startswith("image/"):
                image = web_url(element.get("url"))
            if image:
                break
        articles.append({
            "id": hashlib.sha256(url.encode()).hexdigest()[:24],
            "title": title,
            "description": description,
            "url": url,
            "image": image,
            "source": source,
            "published_at": published,
        })
    return articles


def fetch_source(source, url):
    try:
        request = Request(url, headers={"User-Agent": "ZenSeek/1.0 (news reader)"})
        with urlopen(request, timeout=10) as response:
            content = response.read(MAX_FEED_BYTES + 1)
        if len(content) > MAX_FEED_BYTES:
            raise ValueError("Feed exceeds size limit")
        return parse_feed(content, source)
    except (OSError, ValueError, ET.ParseError) as error:
        print(f"Could not update {source}: {error}", flush=True)
        return []


def save_state():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, DATA_FILE)


def load_state():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data.get("articles"), list) and isinstance(data.get("views"), dict):
            state.update(data)
    except (OSError, ValueError, AttributeError):
        pass


def refresh():
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        results = list(executor.map(lambda pair: fetch_source(*pair), SOURCES.items()))
    articles = [article for result in results for article in result]
    with lock:
        if articles:
            # Keep the last successful articles from sources temporarily unavailable.
            successful = {article["source"] for article in articles}
            articles.extend(a for a in state["articles"] if a["source"] not in successful)
            state["articles"] = list({article["id"]: article for article in articles}.values())
            state["last_updated"] = datetime.now(timezone.utc).isoformat()
            try:
                save_state()
            except OSError as error:
                print(f"Could not save news cache: {error}", flush=True)


def update_loop():
    while True:
        started = time.monotonic()
        refresh()
        time.sleep(max(0, 60 - (time.monotonic() - started)))


class Handler(BaseHTTPRequestHandler):
    def send_content(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/news":
            with lock:
                articles = [
                    {**article, "views": state["views"].get(article["id"], 0)}
                    for article in state["articles"]
                ]
                payload = {"articles": articles, "last_updated": state["last_updated"]}
            self.send_content(200, json.dumps(payload).encode(), "application/json; charset=utf-8")
            return
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if path not in files:
            self.send_error(404)
            return
        filename, content_type = files[path]
        try:
            content = (ROOT / filename).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_content(200, content, content_type)

    def do_POST(self):
        match = re.fullmatch(r"/api/articles/([0-9a-f]{24})/view", urlparse(self.path).path)
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self.send_error(403)
            return
        if not match:
            self.send_error(404)
            return
        article_id = match.group(1)
        with lock:
            if not any(article["id"] == article_id for article in state["articles"]):
                self.send_error(404)
                return
            state["views"][article_id] = state["views"].get(article_id, 0) + 1
            try:
                save_state()
            except OSError as error:
                print(f"Could not save view count: {error}", flush=True)
        self.send_content(204, b"", "application/json")


def main():
    load_state()
    threading.Thread(target=update_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("", port), Handler)
    print(f"ZenSeek is running at http://localhost:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
