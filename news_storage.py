import json
import os
import threading
from contextlib import contextmanager

_schema_lock = threading.Lock()
_initialized_url = None


def database_url():
    return os.environ.get("DATABASE_URL")


@contextmanager
def connection():
    import psycopg

    with psycopg.connect(database_url(), connect_timeout=10) as conn:
        yield conn


def initialize(conn):
    global _initialized_url
    with _schema_lock:
        if _initialized_url == database_url() and _initialized_url is not None:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (1842590702,))
            cur.execute("""
                CREATE TABLE IF NOT EXISTS news_feed (
                    id integer PRIMARY KEY CHECK (id = 1),
                    store jsonb NOT NULL,
                    catalog jsonb NOT NULL,
                    started_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            cur.execute("ALTER TABLE news_feed ADD COLUMN IF NOT EXISTS started_at timestamptz NOT NULL DEFAULT now()")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS news_stories (
                    slug text PRIMARY KEY,
                    item jsonb NOT NULL,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS newsletter_sends (
                    send_date date PRIMARY KEY,
                    status text NOT NULL,
                    campaign_id bigint,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """)
        conn.commit()
        _initialized_url = database_url()


def load_feed():
    with connection() as conn:
        initialize(conn)
        row = conn.execute("SELECT store, catalog FROM news_feed WHERE id = 1").fetchone()
        return row if row else None


def load_story(slug):
    with connection() as conn:
        initialize(conn)
        row = conn.execute("SELECT item FROM news_stories WHERE slug = %s", (slug,)).fetchone()
        return row[0] if row else None


def recent_stories():
    with connection() as conn:
        initialize(conn)
        return [row[0] for row in conn.execute(
            "SELECT item FROM news_stories WHERE updated_at >= now() - interval '7 days' ORDER BY updated_at DESC LIMIT 300"
        ).fetchall()]


def claim_newsletter(send_date):
    with connection() as conn:
        initialize(conn)
        row = conn.execute("""
            INSERT INTO newsletter_sends (send_date, status)
            VALUES (%s, 'claimed') ON CONFLICT (send_date) DO NOTHING
            RETURNING send_date
        """, (send_date,)).fetchone()
        conn.commit()
        return row is not None


def newsletter_attempted(send_date):
    with connection() as conn:
        initialize(conn)
        return conn.execute(
            "SELECT 1 FROM newsletter_sends WHERE send_date = %s", (send_date,)
        ).fetchone() is not None


def update_newsletter(send_date, status, campaign_id=None):
    with connection() as conn:
        initialize(conn)
        conn.execute("""
            UPDATE newsletter_sends SET status = %s,
                campaign_id = COALESCE(%s, campaign_id), updated_at = now()
            WHERE send_date = %s
        """, (status, campaign_id, send_date))
        conn.commit()


def save_feed(conn, store, catalog, started_at):
    with conn.transaction():
        conn.execute("""
            INSERT INTO news_feed (id, store, catalog, started_at, updated_at)
            VALUES (1, %s::jsonb, %s::jsonb, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                store = EXCLUDED.store, catalog = EXCLUDED.catalog,
                started_at = EXCLUDED.started_at, updated_at = now()
        """, (json.dumps(store), json.dumps(catalog), started_at))
        for item in store["items"]:
            conn.execute("""
                INSERT INTO news_stories (slug, item, updated_at)
                VALUES (%s, %s::jsonb, now())
                ON CONFLICT (slug) DO UPDATE SET item = EXCLUDED.item, updated_at = now()
            """, (item["slug"], json.dumps(item)))


@contextmanager
def refresh_lease(force=False):
    with connection() as conn:
        initialize(conn)
        acquired = conn.execute("SELECT pg_try_advisory_lock(%s)", (1842590701,)).fetchone()[0]
        conn.commit()
        if not acquired:
            yield None
            return
        try:
            row = conn.execute(
                "SELECT started_at > now() - interval '30 minutes' FROM news_feed WHERE id = 1"
            ).fetchone()
            started_at = conn.execute("SELECT now()").fetchone()[0]
            conn.commit()
            yield (conn, started_at) if force or not (row and row[0]) else None
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (1842590701,))
            conn.commit()
