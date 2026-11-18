import json
import math
import mimetypes
import os
import threading
import time
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "catalog.json"
STORE_PATH = ROOT / "daily-content.json"


def load_catalog():
    if os.environ.get("DATABASE_URL"):
        from news_storage import load_feed
        feed = load_feed()
        if feed:
            return feed[1]
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_store():
    if os.environ.get("DATABASE_URL"):
        from news_storage import load_feed
        feed = load_feed()
        if feed:
            return feed[0]
    if not STORE_PATH.exists():
        return {"updated_at": None, "date": None, "source": "Google Trends U.S. RSS", "items": []}
    with STORE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def paginate(items, page, page_size):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": len(items),
            "total_pages": math.ceil(len(items) / page_size),
        },
    }


def openapi_spec():
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "ZenSeek API",
            "version": "1.0.0",
            "description": "Catalog and practical calculation endpoints for ZenSeek.",
        },
        "servers": [{"url": "/"}],
        "paths": {
            "/api/v1/health": {
                "get": {"summary": "Service health", "responses": {"200": {"description": "Healthy"}}}
            },
            "/api/v1/items": {
                "get": {
                    "summary": "List and search ZenSeek items",
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "category", "in": "query", "schema": {"type": "string"}},
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}},
                    ],
                    "responses": {"200": {"description": "Paginated catalog"}},
                }
            },
            "/api/v1/items/{slug}": {
                "get": {
                    "summary": "Get one catalog item",
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Catalog item"}, "404": {"description": "Not found"}},
                }
            },
            "/api/v1/categories": {
                "get": {"summary": "List categories", "responses": {"200": {"description": "Categories"}}}
            },
            "/api/v1/trends/daily": {
                "get": {"summary": "Latest generated U.S. daily trend content", "responses": {"200": {"description": "Daily content"}}}
            },
            "/api/v1/agent/run": {
                "post": {"summary": "Run the content agent immediately", "responses": {"200": {"description": "Agent result"}}}
            },
            "/api/v1/calculators/money-clarity": {
                "post": {
                    "summary": "Calculate monthly money clarity",
                    "responses": {"200": {"description": "Calculation"}},
                }
            },
            "/api/v1/tools/breath-pacer": {
                "post": {
                    "summary": "Build a paced breathing session",
                    "responses": {"200": {"description": "Breathing session"}},
                }
            },
        },
    }


SWAGGER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ZenSeek API Documentation</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; background: #f6f7f4; color: #202b25; }
    .swagger-ui .info .title, .swagger-ui h1, .swagger-ui h2 { color: #202b25; font-family: Georgia, serif; }
    .swagger-ui a { color: #2f6d4e; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({ url: "/openapi.json", dom_id: "#swagger-ui", deepLinking: true });
  </script>
</body>
</html>
"""


class ZenSeekHandler(BaseHTTPRequestHandler):
    server_version = "ZenSeekAPI/1.0"

    def send_json(self, status, payload, cache_control=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control or "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status, text, content_type="text/html; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path == "/api/v1/health":
            self.send_json(200, {
                "status": "healthy",
                "service": "zenseek-api",
                "version": "1.0.0",
            }, "no-cache")
            return

        if path == "/openapi.json":
            self.send_json(200, openapi_spec(), "public, max-age=300")
            return

        if path in ("/api-docs", "/docs"):
            self.send_text(200, SWAGGER_HTML)
            return

        if path == "/api/v1/items":
            catalog = load_catalog()
            search = query.get("q", [""])[0].strip().lower()
            category = query.get("category", [""])[0].strip().lower()
            try:
                page = int(query.get("page", ["1"])[0])
                page_size = int(query.get("page_size", ["20"])[0])
            except ValueError:
                self.send_json(400, {"error": {"code": "invalid_pagination", "message": "page and page_size must be integers"}})
                return

            items = catalog["items"]
            if category:
                items = [item for item in items if item["category"].lower() == category]
            if search:
                items = [
                    item for item in items
                    if search in " ".join((
                        item["title"], item["description"], item["category"],
                        " ".join(item["tags"]),
                    )).lower()
                ]
            self.send_json(200, paginate(items, page, page_size), "public, max-age=300")
            return

        if path.startswith("/api/v1/items/"):
            slug = path.rsplit("/", 1)[-1]
            item = next((entry for entry in load_catalog()["items"] if entry["slug"] == slug), None)
            if not item:
                self.send_json(404, {"error": {"code": "item_not_found", "message": f"No item found for slug '{slug}'"}})
                return
            self.send_json(200, item, "public, max-age=300")
            return

        if path == "/api/v1/categories":
            categories = {}
            for item in load_catalog()["items"]:
                categories[item["category"]] = categories.get(item["category"], 0) + 1
            self.send_json(200, {
                "categories": [
                    {"name": name, "count": count}
                    for name, count in sorted(categories.items())
                ]
            }, "public, max-age=300")
            return

        if path == "/api/v1/trends/daily":
            self.send_json(200, load_store(), "no-cache")
            return

        if path.startswith("/story/"):
            slug = path[len("/story/"):].strip("/")
            items = load_store()["items"]
            item = next((entry for entry in items if entry["slug"] == slug), None)
            if not item and os.environ.get("DATABASE_URL"):
                from news_storage import load_story
                item = load_story(slug)
            if item:
                generated_page = (ROOT / item["path"].lstrip("/") / "index.html").resolve()
                if not item.get("content") and ROOT in generated_page.parents and generated_page.is_file():
                    self.serve_static(item["path"].rstrip("/") + "/index.html")
                    return

                from content_agent import render_page, template_generate
                topic = item["topic"]
                category = item["category"].removeprefix("Daily ")
                articles = item["sources"]
                content = item.get("content")
                if content is None:
                    content = template_generate(topic, category, articles)
                    content["headline"] = item["headline"]
                    content["lede"] = item["description"]
                    content["economy_impact"] = item["economy_impact"]
                    content["actions"] = item["actions"]
                self.send_text(200, render_page(
                    topic, category, content, item["published"],
                    next((i for i, entry in enumerate(items, 1) if entry["slug"] == slug), 1),
                    item["traffic"], articles, item["fact_check"], item["economic_impact_score"],
                ))
                return
            match = re.match(r"^daily-(\d{4}-\d{2}-\d{2})-(.+)$", slug)
            if match:
                date, story_slug = match.groups()
                candidate = ROOT / "generated" / date / story_slug / "index.html"
                if candidate.is_file():
                    self.send_redirect(f"/generated/{date}/{story_slug}/index.html")
                    return
            self.send_json(404, {"error": {"code": "story_not_found", "message": "Story not found"}})
            return

        self.serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": {"code": "invalid_json", "message": "Request body must be valid JSON"}})
            return

        if parsed.path == "/api/v1/agent/run":
            token = os.environ.get("AGENT_TOKEN")
            if os.environ.get("DYNO") and not token:
                self.send_json(503, {"error": {"code": "agent_token_required", "message": "AGENT_TOKEN must be configured for manual refreshes"}})
                return
            if token:
                supplied = self.headers.get("Authorization", "")
                if supplied != f"Bearer {token}":
                    self.send_json(401, {"error": {"code": "unauthorized", "message": "A valid agent bearer token is required"}})
                    return
            try:
                from content_agent import run_scheduled
                limit = min(60, max(1, int(body.get("limit", 40))))
                store = run_scheduled(limit, force=True)
                if store is None:
                    self.send_json(409, {"error": {"code": "agent_busy", "message": "A refresh is already running"}})
                    return
                self.send_json(200, {
                    "status": "generated",
                    "generated": len(store["items"]),
                    "date": store["date"],
                    "source": store["source"],
                })
            except Exception as exc:
                self.send_json(502, {
                    "error": {
                        "code": "agent_failed",
                        "message": "The content agent could not update generated content",
                        "detail": str(exc),
                    }
                })
            return

        if parsed.path == "/api/v1/calculators/money-clarity":
            required = ("monthly_income", "monthly_expenses", "monthly_debt")
            missing = [key for key in required if key not in body]
            if missing:
                self.send_json(422, {
                    "error": {
                        "code": "missing_fields",
                        "message": "Missing required fields",
                        "fields": missing,
                    }
                })
                return
            try:
                income = max(0.0, float(body["monthly_income"]))
                expenses = max(0.0, float(body["monthly_expenses"]))
                debt = max(0.0, float(body["monthly_debt"]))
            except (TypeError, ValueError):
                self.send_json(422, {"error": {"code": "invalid_number", "message": "Monetary fields must be numbers"}})
                return

            available = round(income - expenses - debt, 2)
            debt_ratio = round((debt / income * 100) if income else 0.0, 2)
            target_savings = round(income * 0.20, 2)
            self.send_json(200, {
                "monthly_income": income,
                "monthly_expenses": expenses,
                "monthly_debt": debt,
                "monthly_available": available,
                "debt_to_income_percent": debt_ratio,
                "suggested_savings_20_percent": target_savings,
                "status": "positive" if available >= target_savings else "review" if available > 0 else "shortfall",
            })
            return

        if parsed.path == "/api/v1/tools/breath-pacer":
            try:
                inhale = max(1.0, float(body.get("inhale_seconds", 4)))
                hold = max(0.0, float(body.get("hold_seconds", 2)))
                exhale = max(1.0, float(body.get("exhale_seconds", 6)))
                cycles = min(30, max(1, int(body.get("cycles", 5))))
            except (TypeError, ValueError):
                self.send_json(422, {"error": {"code": "invalid_number", "message": "Breathing values must be numbers"}})
                return
            cycle_seconds = inhale + hold + exhale
            self.send_json(200, {
                "inhale_seconds": inhale,
                "hold_seconds": hold,
                "exhale_seconds": exhale,
                "cycles": cycles,
                "cycle_seconds": cycle_seconds,
                "total_seconds": round(cycle_seconds * cycles, 2),
            })
            return

        self.send_json(404, {"error": {"code": "endpoint_not_found", "message": "Endpoint not found"}})

    def serve_static(self, path):
        relative = path.strip("/")
        candidate = ROOT / (relative or "index.html")

        if relative and candidate.is_dir():
            candidate = candidate / "index.html"
        elif not candidate.exists() and not relative.endswith((".html", ".js", ".css", ".json", ".xml", ".webmanifest")):
            candidate = ROOT / f"{relative}.html"
        if not candidate.exists():
            candidate = ROOT / "index.html"
        try:
            candidate = candidate.resolve(strict=True)
        except FileNotFoundError:
            self.send_json(404, {"error": {"code": "not_found", "message": "Resource not found"}})
            return
        if ROOT not in candidate.parents and candidate != ROOT:
            self.send_json(403, {"error": {"code": "forbidden", "message": "Forbidden"}})
            return
        if not candidate.is_file():
            self.send_json(404, {"error": {"code": "not_found", "message": "Resource not found"}})
            return

        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/json", "application/javascript"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache" if candidate.name in ("sw.js", "index.html") else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


def main():
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    
    if os.environ.get("ENABLE_TRENDS_AGENT", "1") != "0" and (
        os.environ.get("DYNO") is None or os.environ.get("DATABASE_URL")
    ):
        def scheduled_content_agent():
            while True:
                try:
                    from content_agent import run_scheduled
                    store = run_scheduled(int(os.environ.get("TRENDS_LIMIT", "40")))
                    if store:
                        print(f"[{time.strftime('%H:%M:%S')}] Content agent generated {len(store['items'])} items and updated daily feed", flush=True)
                except Exception as exc:
                    print(f"[{time.strftime('%H:%M:%S')}] Content agent update failed: {exc}")
                    import traceback
                    traceback.print_exc()
                time.sleep(60)
        threading.Thread(target=scheduled_content_agent, name="trends-agent", daemon=True).start()
    elif os.environ.get("DYNO") and os.environ.get("ENABLE_TRENDS_AGENT", "1") != "0":
        print("Content agent disabled: DATABASE_URL is required on Heroku", flush=True)
    
    server = ThreadingHTTPServer((host, port), ZenSeekHandler)
    print(f"ZenSeek API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
