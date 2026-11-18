import argparse
import html
import json
import os
import re
import ssl
import sys
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TRENDS_URL = "https://trends.google.com/trending/rss?geo=US"
STORE_PATH = ROOT / "daily-content.json"
CATALOG_PATH = ROOT / "catalog.json"
GENERATED_PATH = ROOT / "generated"


def parse_rss_date(date_string):
    """Parse RSS pubDate and return both ISO and readable formats"""
    try:
        # Try parsing RFC 2822 format (used by RSS feeds)
        dt = parsedate_to_datetime(date_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        iso_format = dt.isoformat()
        readable_format = dt.strftime("%I:%M %p UTC • %b %d, %Y")
        return iso_format, readable_format
    except (TypeError, ValueError):
        # Fallback to current time if parsing fails
        now = datetime.now(timezone.utc)
        return now.isoformat(), now.strftime("%I:%M %p UTC • %b %d, %Y")


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:70] or "daily-topic"


def classify(title):
    text = title.lower()
    rules = (
        ("Health", ("health", "hospital", "doctor", "medicine", "disease", "fitness", "covid")),
        ("Money", ("stock", "market", "crypto", "economy", "price", "salary", "money")),
        ("Learning", ("science", "history", "math", "school", "space", "planet")),
        ("Entertainment", ("movie", "music", "game", "show", "concert", "celebrity")),
        ("Sports", ("football", "basketball", "baseball", "soccer", "nfl", "nba", "mlb", "uefa", "nations league", "team news", "match")),
        ("Technology", ("ai", "app", "tech", "phone", "software", "internet")),
    )
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return "Trending"


SOURCE_LEANINGS = {
    "apnews.com": "Center",
    "reuters.com": "Center",
    "bbc.com": "Center",
    "pbs.org": "Center",
    "abcnews.go.com": "Center",
    "cbsnews.com": "Center",
    "nbcnews.com": "Center",
    "usatoday.com": "Center",
    "nytimes.com": "Center-left",
    "washingtonpost.com": "Center-left",
    "theguardian.com": "Center-left",
    "npr.org": "Center-left",
    "cnn.com": "Center-left",
    "wsj.com": "Center-right",
    "foxnews.com": "Right",
    "breitbart.com": "Right",
    "dailycaller.com": "Right",
}


def source_leaning(url, source_name=""):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        host = ""
    host = host.lower().replace("www.", "")
    for domain, leaning in SOURCE_LEANINGS.items():
        if host == domain or host.endswith("." + domain):
            return leaning
    name = source_name.lower()
    name_map = {
        "associated press": "Center",
        "reuters": "Center",
        "bbc": "Center",
        "pbs": "Center",
        "abc": "Center",
        "cbs": "Center",
        "nbc": "Center",
        "usa today": "Center",
        "new york times": "Center-left",
        "washington post": "Center-left",
        "the guardian": "Center-left",
        "npr": "Center-left",
        "cnn": "Center-left",
        "wall street journal": "Center-right",
        "fox news": "Right",
        "breitbart": "Right",
        "daily caller": "Right",
    }
    for publisher, leaning in name_map.items():
        if publisher in name:
            return leaning
    return "Not yet classified"


def get_unsplash_image(query, timeout=5):
    """Fetch image URL from Unsplash Source API"""
    try:
        encoded_query = urllib.parse.quote(query.replace(" ", ","))
        # Using direct image URL from Unsplash (doesn't require auth)
        url = f"https://source.unsplash.com/400x300/?{encoded_query}"
        request = urllib.request.Request(url, headers={"User-Agent": "ZenSeekContentAgent/1.0"})
        context = ssl.create_default_context()
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        
        # Follow redirects to get the actual image URL
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            final_url = response.geturl()
            if final_url and 'unsplash' in final_url.lower():
                return final_url
    except Exception:
        pass
    
    # Fallback: return a placeholder with better structure
    return None


def generate_placeholder_image(title, category, index):
    """Generate a data URI for a placeholder image"""
    # Color palette by category
    colors = {
        "Money": "E74C3C",
        "Technology": "3498DB", 
        "Health": "27AE60",
        "Learning": "9B59B6",
        "Entertainment": "F39C12",
        "Sports": "E91E63",
        "Trending": "16A085",
    }
    
    color = colors.get(category, "16A085")
    # Encode the title for display
    title_short = (title[:25] + "...") if len(title) > 25 else title
    title_encoded = title_short.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
      <defs>
        <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color:#{color};stop-opacity:1" />
          <stop offset="100%" style="stop-color:#{color}bb;stop-opacity:1" />
        </linearGradient>
      </defs>
      <rect width="400" height="300" fill="url(#grad)"/>
      <rect width="400" height="300" fill="rgba(0,0,0,0.3)"/>
      <text x="200" y="140" font-size="18" font-weight="bold" fill="white" text-anchor="middle" font-family="Arial,sans-serif">{title_encoded}</text>
      <text x="200" y="260" font-size="14" fill="rgba(255,255,255,0.9)" text-anchor="middle" font-family="Arial,sans-serif">📰 News Story</text>
    </svg>"""
    
    encoded = urllib.parse.quote(svg)
    return f"data:image/svg+xml,{encoded}"


def fetch_topic_news(topic, limit=8, timeout=12):
    query = urllib.parse.quote(topic)
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    request = urllib.request.Request(url, headers={"User-Agent": "ZenSeekContentAgent/1.0"})
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            root = ET.fromstring(response.read())
    except Exception:
        return []

    articles = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        published = item.findtext("pubDate", default="")
        raw_description = item.findtext("description", default="")
        description = re.sub(r"<[^>]+>", " ", raw_description)
        description = re.sub(r"\s+", " ", html.unescape(description)).strip()
        description = description.replace("&nbsp;", " ").replace("&amp;", "&").replace("nbsp;", " ")
        description = description.replace("&nbsp;", " ").replace("&amp;", "&").replace("nbsp;", " ")
        source_name = ""
        source = item.find("source")
        if source is not None:
            source_name = source.text or ""
        
        # Try to get real image first, fallback to placeholder
        image_url = get_unsplash_image(topic, timeout=2)
        if not image_url:
            image_url = generate_placeholder_image(title, classify(topic), len(articles))
        
        articles.append({
            "title": " ".join(title.split()),
            "source": source_name,
            "url": link,
            "published": published,
            "leaning": source_leaning(link, source_name),
            "description": description[:800],
            "image": image_url,
        })
        if len(articles) >= limit:
            break
    return articles


def fetch_major_news(limit=20, timeout=12):
    url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    request = urllib.request.Request(url, headers={"User-Agent": "ZenSeekContentAgent/1.0"})
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            root = ET.fromstring(response.read())
    except Exception:
        return []

    headlines = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        title = clean_headline(title)  # Remove boilerplate phrases
        link = item.findtext("link", default="")
        source = item.find("source")
        source_name = source.text if source is not None and source.text else ""
        raw_description = item.findtext("description", default="")
        description = re.sub(r"<[^>]+>", " ", raw_description)
        description = re.sub(r"\s+", " ", html.unescape(description)).strip()
        description = description.replace("&nbsp;", " ").replace("&amp;", "&").replace("nbsp;", " ")
        
        # Try to get real image first, fallback to placeholder
        keywords = " ".join(title.split()[:3])  # First 3 words as search terms
        image_url = get_unsplash_image(keywords, timeout=2)
        if not image_url:
            image_url = generate_placeholder_image(title, classify(title), len(headlines))
        
        headlines.append({
            "title": " ".join(title.split()),
            "source": source_name,
            "url": link,
            "published": item.findtext("pubDate", default=""),
            "leaning": source_leaning(link, source_name),
            "description": description[:800],
            "image": image_url,
        })
        if len(headlines) >= limit:
            break
    network_feeds = (
        ("Associated Press", "apnews.com"),
        ("Reuters", "reuters.com"),
        ("ABC News", "abcnews.go.com"),
        ("CBS News", "cbsnews.com"),
        ("NBC News", "nbcnews.com"),
        ("CNN", "cnn.com"),
        ("NPR", "npr.org"),
        ("PBS", "pbs.org"),
        ("The New York Times", "nytimes.com"),
        ("The Washington Post", "washingtonpost.com"),
        ("The Wall Street Journal", "wsj.com"),
        ("Fox News", "foxnews.com"),
    )
    seen = {(item["title"], item["source"]) for item in headlines}
    for publisher, domain in network_feeds:
        network_url = (
            "https://news.google.com/rss/search?"
            + urllib.parse.urlencode({
                "q": f"when:1d site:{domain}",
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            })
        )
        request = urllib.request.Request(network_url, headers={"User-Agent": "ZenSeekContentAgent/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                network_root = ET.fromstring(response.read())
        except Exception:
            continue
        for item in network_root.findall(".//item"):
            title = " ".join(item.findtext("title", default="").split())
            title = clean_headline(title)  # Remove boilerplate phrases
            link = item.findtext("link", default="")
            source = item.find("source")
            source_name = source.text if source is not None and source.text else publisher
            raw_description = item.findtext("description", default="")
            description = re.sub(r"<[^>]+>", " ", raw_description)
            description = re.sub(r"\s+", " ", html.unescape(description)).strip()
            key = (title, source_name)
            if title and key not in seen:
                seen.add(key)
                headlines.append({
                    "title": title,
                    "source": source_name,
                    "url": link,
                    "published": item.findtext("pubDate", default=""),
                    "leaning": source_leaning(link, source_name),
                    "publisher_group": publisher,
                    "description": description[:800],
                })
            if len(headlines) >= limit:
                return headlines
    return headlines


def fetch_trends(limit=12, timeout=12):
    request = urllib.request.Request(
        TRENDS_URL,
        headers={"User-Agent": "ZenSeekContentAgent/1.0"},
    )
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        body = response.read()
    root = ET.fromstring(body)
    trends = []
    for item in root.findall(".//item"):
        title_node = item.find("title")
        if title_node is None or not title_node.text:
            continue
        title = " ".join(title_node.text.split())
        traffic = item.findtext("ht:approx_traffic", default="", namespaces={"ht": "https://trends.google.com/trends"})
        trends.append({
            "title": title,
            "traffic": traffic,
            "source_url": item.findtext("link", default=""),
            "published": item.findtext("pubDate", default=""),
        })
        if len(trends) >= limit:
            break
    return trends


ECONOMIC_HIGH = (
    "fed", "federal reserve", "interest rate", "inflation", "jobs", "unemployment",
    "gdp", "recession", "tax", "tariff", "trade", "economy", "market", "stock",
    "oil", "gas", "energy", "mortgage", "bank", "dollar", "government shutdown",
    "congress", "senate", "house", "president", "white house", "election",
)
ECONOMIC_MEDIUM = (
    "health", "hospital", "medicine", "drug", "insurance", "weather", "hurricane",
    "technology", "ai", "cyber", "outage", "security", "immigration", "court",
)


def economic_impact_score(subject, category):
    text = subject.lower()
    score = 10
    if category == "Money":
        score += 45
    if category == "Technology":
        score += 20
    if category == "Health":
        score += 18
    if category == "Sports":
        score += 2
    if category == "Entertainment":
        score -= 8
    for term in ECONOMIC_HIGH:
        if term in text:
            score += 24
    for term in ECONOMIC_MEDIUM:
        if term in text:
            score += 12
    return min(100, max(1, score))


def source_collector_agent(topic):
    return fetch_topic_news(topic, limit=12)


def fact_check_agent(topic, articles):
    sources = [article["source"] for article in articles if article.get("source")]
    unique_sources = list(dict.fromkeys(sources))
    leanings = [article["leaning"] for article in articles if article.get("leaning")]
    unique_leanings = list(dict.fromkeys(leanings))
    if len(unique_sources) >= 3:
        confidence = "Moderate to high"
        status = "Multiple named outlets are reporting this headline."
    elif unique_sources:
        confidence = "Preliminary"
        status = "Limited independent source confirmation is currently available."
    else:
        confidence = "Low"
        status = "No named reporting source was found in the current feed."
    return {
        "confidence": confidence,
        "status": status,
        "named_sources": unique_sources,
        "source_count": len(unique_sources),
        "leanings": unique_leanings,
        "limitations": (
            "This pass compares headline and outlet signals from public RSS feeds. "
            "It does not claim to read full paywalled reporting or verify every underlying claim."
        ),
    }


def ranking_agent(subject, category, traffic, source_count):
    score = economic_impact_score(subject, category)
    if traffic:
        digits = re.findall(r"\d+", traffic.replace(",", "").replace("+", ""))
        if digits:
            score += min(12, len(digits) * 2)
    score += min(8, source_count)
    return min(100, score)


def llm_generate(topic, category, articles, api_key=None):
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    article_context = "\n".join(
        f"- {article['title']} | {article['source']} | {article['leaning']}"
        for article in articles
    )
    prompt = (
        "Create an accessible, source-aware U.S. news story for this Google Trends topic: "
        f"{topic}. Category: {category}. Use only the following headline context; do not invent facts: \n"
        f"{article_context}\n"
        "Return strict JSON with these keys: "
        "headline (a detailed, neutral newsroom headline), subheadline (plain-language explanation), "
        "lede (2-3 sentences), story (8-12 detailed short paragraphs in an array), "
        "what_we_know (array of 5 factual points), what_remains_unknown (array of 4 uncertainty points), "
        "why_it_matters (array of 5 audience-impact points), economy_impact (detailed paragraph), "
        "actions (array of 4 practical items), source_lens (one detailed paragraph), "
        "what_to_watch (array of 5 next developments), and bottom_line (2-3 sentence conclusion). "
        "Write as a real general-news correspondent would for a broad U.S. audience in easy-to-understand language. "
        "Use a clear, modern tone useful to Gen Z and Millennials, without slang-heavy copy or invented details. "
        "Clearly distinguish reported facts, probable interpretations, and uncertainty. Do not invent quotations or figures."
    )
    payload = json.dumps({
        "model": model,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You create factual, accessible educational content. Never invent current facts."},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        result = json.loads(response.read())
    content = json.loads(result["choices"][0]["message"]["content"])
    return content


def clean_headline(text):
    """Remove common boilerplate phrases from news headlines"""
    if not text:
        return text
    
    # Remove phrases commonly added by news outlets
    # Use more flexible separator matching (colon, dash, em-dash, en-dash, etc.)
    remove_patterns = [
        r"[\s:—–\-]*[Ww]hat you need to know\s*$",
        r"[\s:—–\-]*[Ww]hat to know\s*$",
        r"[\s:—–\-]*[Hh]ere's what you need to know\s*$",
        r"[\s:—–\-]*[Hh]ere's what we know\s*$",
        r"[\s:—–\-]*[Hh]ere's the latest\s*$",
        r"[\s:—–\-]*[Ll]ive updates\s*$",
        r"[\s:—–\-]*Breaking news\s*$",
        r"[\s:—–\-]*[Ff]ast facts\s*$",
        r"[\s:—–\-]*[Cc]omplete guide\s*$",
        r"[\s:—–\-]*[Ee]xplained\s*$",
    ]
    
    for pattern in remove_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    
    return text.strip()


def clean_description(text):
    """Create a clean, concise description from article content"""
    if not text:
        return ""
    
    # Remove repetitive starter phrases
    starters = [
        "Recent reporting describes",
        "A plain-language briefing",
        "Recent reports indicate",
        "New reporting shows",
        "Here's what we know",
        "Here's the latest",
        "According to reports",
        "The latest reporting",
        "Current reporting",
        "Breaking news",
        "News update:",
        "Here's what's happening:",
        "is making headlines",
    ]
    
    lower_text = text.lower()
    for starter in starters:
        if lower_text.startswith(starter.lower()):
            text = text[len(starter):].lstrip(" -:,")
            break
    
    # Truncate to first sentence or 200 chars, whichever comes first
    text = text.strip()
    sentences = text.split(". ")
    if sentences:
        first_sentence = sentences[0].strip()
        if len(first_sentence) > 160:
            # If first sentence too long, truncate at word boundary
            truncated = first_sentence[:160]
            last_space = truncated.rfind(" ")
            if last_space > 0:
                truncated = truncated[:last_space]
            first_sentence = truncated.rstrip() + "…"
        return first_sentence.rstrip() if first_sentence else ""
    
    return text[:200].rstrip() + "…" if len(text) > 200 else text


def template_generate(topic, category, articles=None):
    articles = articles or []
    reporting = []
    for article in articles[:3]:
        title = article.get("title", "")
        source = article.get("source", "")
        description = article.get("description", "")
        if description:
            reporting.append(f"{description[:280]}" + (f" ({source})" if source else ""))
        elif title:
            reporting.append(f"{title}" + (f" ({source})" if source else ""))
    reporting_context = " ".join(reporting)
    category_context = {
        "Health": "health guidance or a medical development",
        "Money": "a financial market move, price, or economic decision",
        "Learning": "an educational, science, or historical development",
        "Entertainment": "a release, public appearance, or entertainment announcement",
        "Sports": "a match result, roster change, injury, or competition",
        "Technology": "a product update, outage, policy, or technology announcement",
        "Trending": "a breaking or widely discussed public development",
    }.get(category, "a breaking or widely discussed public development")
    
    # Create a cleaner lede without repetitive starters
    lede_text = f"{topic} is making headlines. {reporting_context or category_context}. Check the source links below for the full story from reputable outlets."
    
    return {
        "headline": f"{topic}",
        "subheadline": f"Here's what's happening with {topic}.",
        "lede": lede_text,
        "story": [
            f"The story: {reporting_context or topic}",
            f"Why now: {category_context}",
            "What's happening: News coverage is growing because this affects public interest and potentially government policy, markets, or daily life.",
            "Sources: Check the links below to read full reporting from reputable outlets across the political spectrum.",
            "What to watch: Monitor official statements and news updates as the story develops.",
        ],
        "what_we_know": [
            f"This story is trending nationally today.",
            "Multiple reputable news outlets are reporting on it.",
            f"It falls under the {category.lower()} category.",
            "Real sources matter more than speed—check the links below.",
            "News updates regularly; this article will be refreshed with new information.",
        ],
        "what_remains_unknown": [
            "The full scope and consequences may not be clear yet.",
            "Official statements or confirmations may still be pending.",
            "Local impacts may differ from national headlines.",
            "More details will likely emerge as sources report further.",
        ],
        "why_it_matters": [
            "It may affect your area, job, finances, or family plans.",
            "Government or business decisions could follow.",
            "Understanding the story helps you make informed decisions.",
            "It reflects broader trends worth paying attention to.",
            "Accurate information prevents rumors from spreading.",
        ],
        "economy_impact": (
            f"Economic impact depends on what happens next. {topic} could affect prices, jobs, regulations, or public services—or it might be localized. Wait for confirmed details."
        ),
        "actions": [
            "Read the source links for the full context—don't stop at headlines.",
            "Check multiple outlets with different perspectives.",
            "Watch for official statements and updates.",
            "Share verified information, not speculation.",
        ],
        "source_lens": (
            "News outlets have different editorial perspectives. Reading across center, left, and right sources gives you a fuller picture."
        ),
        "what_to_watch": [
            "Official statements and next steps",
            "Updates from verified news sources",
            "Real-world impacts and confirmed details",
            "Government or business responses",
            "Corrections if details change",
        ],
        "bottom_line": (
            f"{topic} matters, but stick to verified reporting. Check the sources below and update your understanding as new information arrives."
        )
    }


def render_page(topic, category, content, date, rank, traffic, articles, fact_check, economic_score):
    escaped_topic = html.escape(topic)
    story = "".join(f"<p>{html.escape(item)}</p>" for item in content.get("story", []))
    known = "".join(f"<li>{html.escape(item)}</li>" for item in content.get("what_we_know", []))
    unknown = "".join(f"<li>{html.escape(item)}</li>" for item in content.get("what_remains_unknown", []))
    why = "".join(f"<li>{html.escape(item)}</li>" for item in content.get("why_it_matters", []))
    actions = "".join(f"<li>{html.escape(item)}</li>" for item in content.get("actions", []))
    watch = "".join(f"<li>{html.escape(item)}</li>" for item in content.get("what_to_watch", []))
    sources = "".join(
        f'<li><a href="{html.escape(article["url"], quote=True)}" rel="noopener noreferrer" target="_blank">'
        f'{html.escape(article["title"])}</a>'
        f'<span class="source">{html.escape(article["source"] or "Source name unavailable")} · '
        f'{html.escape(article["leaning"])}</span></li>'
        for article in articles
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(content.get("headline", escaped_topic))} | ZenSeek Daily Briefing</title>
  <meta name="description" content="A useful, source-aware overview of {escaped_topic}.">
  <link rel="canonical" href="/story/daily-{date}-{slugify(topic)}">
  <style>
    :root{{color-scheme:dark light;font-family:system-ui,sans-serif}}
    body{{margin:0;background:#101820;color:#eef6f9}}main{{max-width:980px;margin:auto;padding:32px 20px}}
    a{{color:#e7c55b}}.eyebrow{{letter-spacing:.12em;text-transform:uppercase;color:#e7c55b}}
    h1{{font-size:clamp(2.2rem,6vw,4.5rem);line-height:1.03;margin:.25em 0}}
    .subheadline{{font-size:clamp(1.15rem,2.4vw,1.45rem);line-height:1.45;color:#dce8ed;max-width:820px}}
    .lede{{font-size:1.2rem;line-height:1.65;border-left:4px solid #e7c55b;padding-left:16px}}
    .card{{background:#172733;border:1px solid #35505f;border-radius:16px;padding:22px;margin:18px 0}}
    .card h2{{margin-top:0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px}}
    .story p{{font-size:1.08rem;line-height:1.72;margin:.9em 0}}
    .source{{display:block;color:#aebfc8;font-size:.85rem;margin-top:4px}}
    li{{margin:.55em 0}}.source-list{{padding-left:20px}}
    .leaning{{display:inline-block;padding:4px 9px;border-radius:999px;background:#253d48;font-size:.82rem}}
    .bottom{{border:2px solid #e7c55b}}.meta{{color:#b9c9d4}}
    .disclaimer{{font-size:.9rem;color:#b9c9d4}}
  </style>
</head>
<body><main>
  <a href="/">ZenSeek</a><p class="eyebrow">Daily briefing #{rank} · {html.escape(category)} · U.S. economic impact</p>
  <h1>{html.escape(content.get("headline", escaped_topic))}</h1>
  <p class="subheadline">{html.escape(content.get("subheadline", ""))}</p>
  <p class="lede">{html.escape(content.get("lede", ""))}</p>
  <p class="meta">ZenSeek Daily Desk · {html.escape(date)} · {html.escape(traffic or "Search traffic not provided")}</p>
  <article class="card story"><h2>The story</h2>{story}</article>
  <div class="grid">
    <section class="card"><h2>What we know</h2><ul>{known}</ul></section>
    <section class="card"><h2>What remains unknown</h2><ul>{unknown}</ul></section>
  </div>
  <section class="card"><h2>Why it matters</h2><ul>{why}</ul></section>
  <section class="card"><h2>Potential U.S. economic impact</h2><p>{html.escape(content.get("economy_impact", ""))}</p></section>
  <section class="card"><h2>Practical actions</h2><ul>{actions}</ul></section>
  <section class="card"><h2>Reporting and media lens</h2>
    <p>{html.escape(content.get("source_lens", ""))}</p>
    <p class="leaning">Source labels: Center · Center-left · Center-right · Right · Not yet classified</p>
  </section>
  <section class="card"><h2>Fact-check status</h2>
    <p><strong>{html.escape(fact_check["confidence"])} confidence</strong> · {html.escape(fact_check["status"])}</p>
    <p><strong>Named sources checked:</strong> {html.escape(", ".join(fact_check["named_sources"]) or "None identified")}</p>
    <p>{html.escape(fact_check["limitations"])}</p>
  </section>
  <section class="card"><h2>U.S. economic impact ranking</h2>
    <p><strong>{economic_score}/100</strong> · A relative editorial priority score based on economic and policy sensitivity, not a prediction of market movement.</p>
  </section>
  <section class="card"><h2>What to watch next</h2><ul>{watch}</ul></section>
  <section class="card"><h2>Reputable source links</h2>
    <p>Open-reporting links from Google News. Paywalled sites are not bypassed; use summaries, public releases, and outlet links where available.</p>
    <ul class="source-list">{sources or "<li>Source links will appear with the next news update.</li>"}</ul>
  </section>
  <section class="card bottom"><h2>Bottom line</h2><p>{html.escape(content.get("bottom_line", ""))}</p></section>
  <p class="disclaimer">Generated from public reporting feeds across major U.S. and international newsrooms. This is an original summary, not a copy of any outlet's full article.
  Source snapshot: {html.escape(date)}.</p>
</main>
</body></html>
"""


def run(limit=40):
    major_news = fetch_major_news(limit=100)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated = []
    subjects = []
    seen = set()

    for headline in major_news:
        raw_title = headline["title"]
        raw_title = clean_headline(raw_title)  # Remove boilerplate phrases first
        topic = re.sub(r"\s+-\s+[^-]+$", "", raw_title).strip()
        key = slugify(topic)
        if topic and key not in seen:
            seen.add(key)
            subjects.append({
                "topic": topic,
                "traffic": "",
                "source_url": headline["url"],
                "published": headline["published"],
                "origin": "Major U.S. news",
                "source_name": headline.get("source", ""),
                "publisher_group": headline.get("publisher_group", ""),
            })

    prepared = []
    for subject in subjects[:limit]:
        topic = subject["topic"]
        category = classify(topic)
        articles = source_collector_agent(topic)
        content = llm_generate(topic, category, articles)
        if content is None:
            content = template_generate(topic, category, articles)
        fact_check = fact_check_agent(topic, articles)
        economic_score = ranking_agent(topic, category, subject["traffic"], fact_check["source_count"])
        prepared.append({
            "subject": subject,
            "topic": topic,
            "category": category,
            "articles": articles,
            "content": content,
            "fact_check": fact_check,
            "economic_score": economic_score,
        })

    prepared.sort(key=lambda entry: entry["economic_score"], reverse=True)

    for rank, entry in enumerate(prepared, start=1):
        topic = entry["topic"]
        category = entry["category"]
        articles = entry["articles"]
        content = entry["content"]
        fact_check = entry["fact_check"]
        subject = entry["subject"]
        slug = slugify(topic)
        directory = GENERATED_PATH / date / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            render_page(
                topic, category, content, date, rank, subject["traffic"], articles,
                fact_check, entry["economic_score"],
            ),
            encoding="utf-8",
        )
        # Clean up the description
        clean_lede = clean_description(content.get("lede") or content.get("subheadline") or "")
        
        # Get timestamp from first article's published date if available
        article_published = ""
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        timestamp_readable = datetime.now(timezone.utc).strftime("%I:%M %p UTC • %b %d, %Y")
        
        if articles and articles[0].get("published"):
            article_published = articles[0]["published"]
            timestamp_iso, timestamp_readable = parse_rss_date(article_published)
        
        item = {
            "slug": f"daily-{date}-{slug}",
            "title": content.get("headline", f"{topic}"),
            "headline": content.get("headline", f"{topic}"),
            "topic": topic,
            "description": clean_lede,
            "category": f"Daily {category}",
            "image": articles[0].get("image") if articles else None,
            "tags": [category.lower(), "google-trends", "daily", "us"],
            "path": f"/generated/{date}/{slug}/",
            "published": date,
            "published_at": timestamp_iso,
            "published_readable": timestamp_readable,
            "source": subject["origin"],
            "traffic": subject["traffic"],
            "sources": articles,
            "economy_impact": content.get("economy_impact", ""),
            "actions": content.get("actions", []),
            "economic_impact_score": entry["economic_score"],
            "fact_check": fact_check,
            "content": content,
        }
        generated.append(item)

    store = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "source": "Major U.S. and international news feeds",
        "major_news": major_news,
        "items": generated,
    }
    STORE_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing = [item for item in catalog["items"] if not str(item["slug"]).startswith("daily-")]
    catalog["items"] = existing + generated
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return store


def main():
    parser = argparse.ArgumentParser(description="Generate useful ZenSeek content from U.S. Google Trends.")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    try:
        store = run(args.limit)
        print(json.dumps({"generated": len(store["items"]), "date": store["date"]}))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
