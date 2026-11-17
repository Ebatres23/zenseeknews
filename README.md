# ZenSeek News

A calm, mobile-friendly news reader. ZenSeek gathers headlines and descriptions
from BBC News, The Guardian, and NPR RSS feeds and links to the original stories.

## Run locally

Python 3 is the only dependency. Internet access is needed to fetch the feeds.

```sh
python3 server.py
```

Open [http://localhost:8000](http://localhost:8000). Set `PORT` to use a
different port, for example `PORT=8080 python3 server.py`.

The server refreshes its feeds every minute, and open pages check for updates
every minute. Articles and click counts are cached in `data/news.json`; this
directory is created automatically and is not tracked by Git. If a source is
temporarily unavailable, its last cached stories remain visible. On a first
run without network access, the page displays an empty state until a feed can
be reached.

The featured story is the newest article. The remaining stories can be sorted
by their source publication time or by the number of times readers have opened
them from this ZenSeek installation. Those counts are local, not publisher
popularity statistics. Images come from the feeds where available, with an SVG
fallback otherwise.

## Tests

```sh
python3 -m unittest -v test_server
```
