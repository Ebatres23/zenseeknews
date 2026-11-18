# ZenSeek Agent API

ZenSeek is an API and PWA that combines the original site with a news content agent.

The agent reads public news RSS feeds and uses an OpenAI-compatible model to create
source-aware news briefings, which are exposed through the API.

## Run locally

```powershell
python app.py
```

Open `http://localhost:8080/docs` for interactive API documentation.

## Deploy to Heroku

Deploy this repository from its root with the Python buildpack. The `Procfile`
starts the API web process, which binds to Heroku's assigned `PORT`. The
`.python-version` file selects Python 3.11, matching the Docker configuration.

After deployment, check `https://<your-app>.herokuapp.com/api/v1/health`.
Interactive API documentation is available at `/docs`.

The homepage displays article dates in UTC without times. The API retains the
original timestamps for ordering and other consumers.

The Netlify site proxies story URLs to Heroku. Deploy both services after
changing story routing.

Provision Heroku Postgres and attach it to the app so `DATABASE_URL` is set.
Set `OPENAI_API_KEY` on Heroku for the model; this incurs API costs. The web
dyno checks for a refresh every minute, starts a refresh when the last successful
one started at least 30 minutes ago, and uses a Postgres advisory lock to prevent
overlapping refreshes across dynos. An always-running web dyno is required.
News, catalog updates, and story pages are stored in Postgres across restarts.
If the database or model is unavailable, the agent logs the failure and keeps
the last successful feed. Until the first successful refresh, the bundled
feed is displayed. Multiple web dynos must share the same database.

## API

- `GET /api/v1/health`
- `GET /api/v1/items?q=&category=&page=&page_size=`
- `GET /api/v1/items/{slug}`
- `GET /api/v1/categories`
- `POST /api/v1/calculators/money-clarity`
- `POST /api/v1/tools/breath-pacer`
- `GET /api/v1/trends/daily`
- `POST /api/v1/agent/run`

Example:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/v1/calculators/money-clarity" -ContentType "application/json" -Body '{"monthly_income":5000,"monthly_expenses":3000,"monthly_debt":500}'
```

## Run the content agent

```powershell
python content_agent.py --limit 12
```

The server checks for a refresh on startup and starts refreshes at least 30
minutes apart. Set `ENABLE_TRENDS_AGENT=0`
to disable scheduled updates. Without Postgres, local updates are stored in
`daily-content.json` and `catalog.json`; Heroku requires `DATABASE_URL` for
scheduled updates.

Set `AGENT_TOKEN` to require `Authorization: Bearer <token>` for
`POST /api/v1/agent/run`. Heroku rejects manual runs when this token is unset.

Set `OPENAI_API_KEY` and optionally `OPENAI_BASE_URL` and `OPENAI_MODEL`.
Briefings use headlines and short summaries from Google News and matching
publisher RSS feeds, not necessarily full articles. The agent requests detailed,
original summaries and interesting details only when supported by the sources;
some stories will have limited context. Images are omitted when unavailable.
Inspect Heroku logs for feed or model errors.

Story discovery includes Associated Press and KOLO 8 through Google News
site searches, as well as local RSS feeds from WFMY News 2 (North Carolina),
KING 5 (Washington) and WUSA9 (Washington, D.C.). The agent also searches for
`mynews2.com`; results depend on that site's availability in Google News.
Part of each refresh is reserved for distinct outlets when results are available.
Google News results link through Google; direct RSS results link to the publisher.
If an outlet has no usable results, it cannot be included in that refresh.
