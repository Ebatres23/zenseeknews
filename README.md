# ZenSeek Agent API

ZenSeek is a modernized, dependency-free API and PWA that combines the original site with a daily content agent.

The agent reads the public U.S. Google Trends RSS snapshot, creates source-aware educational pages and quizzes,
updates `catalog.json`, and exposes the latest content through the API.

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

The server starts the agent automatically once, then refreshes every 24 hours. Set `ENABLE_TRENDS_AGENT=0` to disable
scheduled updates.

Set `AGENT_TOKEN` to require `Authorization: Bearer <token>` for `POST /api/v1/agent/run`.

The agent uses deterministic, source-aware templates by default. To let an OpenAI-compatible endpoint improve summaries,
set `OPENAI_API_KEY`, optionally `OPENAI_BASE_URL`, and `OPENAI_MODEL`.
