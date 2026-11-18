import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import news_storage


SITE_URL = "https://zenseek.org"


def configuration():
    if os.environ.get("ENABLE_NEWSLETTER") != "1":
        return None
    names = ("DATABASE_URL", "BREVO_API_KEY", "BREVO_LIST_ID", "BREVO_SENDER_EMAIL",
             "BREVO_SIGNUP_URL", "NEWSLETTER_POSTAL_ADDRESS")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if not all(values.values()):
        raise ValueError("Newsletter configuration is incomplete")
    url = urllib.parse.urlparse(values["BREVO_SIGNUP_URL"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or
            not (url.hostname == "sibforms.com" or url.hostname.endswith(".sibforms.com"))):
        raise ValueError("BREVO_SIGNUP_URL must be an HTTPS Brevo signup form URL")
    try:
        list_id = int(values["BREVO_LIST_ID"])
        hour = int(os.environ.get("NEWSLETTER_HOUR_UTC", "15"))
    except ValueError as exc:
        raise ValueError("Newsletter list ID and hour must be integers") from exc
    if list_id <= 0 or not 0 <= hour <= 23:
        raise ValueError("Newsletter list ID or UTC hour is out of range")
    values["list_id"] = list_id
    values["hour"] = hour
    return values


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def digest_html(items, recent, now, address):
    def link(item):
        return SITE_URL + "/story/" + urllib.parse.quote(item["slug"], safe="")

    top = [item for item in items if item.get("slug") and item.get("content")][:5]
    if not top:
        raise ValueError("No generated stories are available for the daily newsletter")
    selected = {item["slug"] for item in top}
    stories = "".join(
        f'<li><a href="{html.escape(link(item), quote=True)}">{html.escape(item["headline"])}</a>'
        f'<p>{html.escape(item.get("description", ""))}</p></li>' for item in top
    )
    watch = []
    for item in recent:
        slug = item.get("slug")
        published = parse_time(item.get("published_at"))
        content = item.get("content") or {}
        details = content.get("what_to_watch", [])
        if (not slug or slug in selected or not published or
                not now - timedelta(days=7) <= published <= now or
                not isinstance(details, list)):
            continue
        detail = next((value.strip() for value in details if isinstance(value, str) and value.strip()), "")
        if detail:
            watch.append(
                f'<li><a href="{html.escape(link(item), quote=True)}">{html.escape(item["headline"])}</a>'
                f' — {html.escape(detail)}</li>'
            )
            selected.add(slug)
        if len(watch) == 3:
            break
    watch_section = f"<h2>Stories to follow this week</h2><ul>{''.join(watch)}</ul>" if watch else ""
    return (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#202b25">'
        '<h1>A little clarity, every day.</h1><p>A quiet moment with today’s news.</p>'
        f"<h2>Today's briefing</h2><ul>{stories}</ul>{watch_section}"
        f'<p><a href="{SITE_URL}/">Read more at ZenSeek</a></p>'
        f'<p>{html.escape(address)}<br><a href="{{{{ unsubscribe }}}}">Unsubscribe</a></p>'
        "</body></html>"
    )


def brevo_request(path, api_key, payload=None):
    request = urllib.request.Request(
        "https://api.brevo.com/v3" + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else b"",
        headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(100_001)
    if len(body) > 100_000:
        raise ValueError("Brevo response exceeded size limit")
    return json.loads(body) if body else {}


def run_daily(now=None):
    config = configuration()
    if config is None:
        return None
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    if now.hour < config["hour"]:
        return None
    send_date = now.date()
    if news_storage.newsletter_attempted(send_date):
        return None
    feed = news_storage.load_feed()
    if not feed:
        raise RuntimeError("Newsletter not sent: no saved news feed")
    store = feed[0]
    updated = parse_time(store.get("updated_at"))
    if not updated or not now - timedelta(hours=48) <= updated <= now:
        raise RuntimeError("Newsletter not sent: news feed is stale or has no update time")
    body = digest_html(store.get("items", []), news_storage.recent_stories(), now,
                       config["NEWSLETTER_POSTAL_ADDRESS"])
    if not news_storage.claim_newsletter(send_date):
        return None
    campaign_id = None
    try:
        campaign = brevo_request("/emailCampaigns", config["BREVO_API_KEY"], {
            "name": f"ZenSeek daily briefing {send_date.isoformat()}",
            "subject": "Your daily moment of clarity",
            "sender": {"name": "ZenSeek", "email": config["BREVO_SENDER_EMAIL"]},
            "recipients": {"listIds": [config["list_id"]]},
            "htmlContent": body,
        })
        campaign_id = campaign["id"]
        if not isinstance(campaign_id, int) or isinstance(campaign_id, bool) or campaign_id <= 0:
            raise ValueError("Brevo returned an invalid campaign ID")
        news_storage.update_newsletter(send_date, "sending", campaign_id)
        brevo_request(f"/emailCampaigns/{campaign_id}/sendNow", config["BREVO_API_KEY"])
        news_storage.update_newsletter(send_date, "submitted", campaign_id)
    except (OSError, ValueError, KeyError) as exc:
        news_storage.update_newsletter(send_date, "failed", campaign_id)
        raise RuntimeError("Newsletter attempt failed; check Brevo before retrying") from exc
    return campaign_id
