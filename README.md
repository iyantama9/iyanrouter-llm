# IyanRouter LLM

A self-hosted LLM routing proxy with a web dashboard. It exposes an Anthropic-compatible API endpoint so tools like Claude Code can talk to multiple upstream providers (Kimchi, OpenModel, Cavoti, BluesMinds) through a single URL, while managing API keys, usage logs, and chat sessions from a browser UI.

## Features

- **Anthropic API-compatible proxy** — forwards Claude Code requests to upstream OpenAI-compatible providers and streams responses back.
- **Multi-provider routing** — supports Kimchi, OpenModel, Cavoti, and BluesMinds with provider-aware key rotation.
- **Web dashboard** — Jinja2 + Tailwind admin UI for stats, request logs, API key management, and chat sessions (backed by PostgreSQL).
- **Key rotation & failover** — automatically rotates and disables exhausted/failing API keys.
- **Persistent state** — request logs, sessions, messages, and key statuses are stored in PostgreSQL (Neon) and reloaded on startup.

## Tech Stack

- Python 3.10+
- FastAPI + Uvicorn
- asyncpg (PostgreSQL)
- Jinja2 templates + Tailwind CSS
- bcrypt for admin password hashing
- httpx for upstream requests

## Setup

1. Copy the example environment file and fill in your values:

   ```bash
   cp .env.example .env
   ```

   Required variables:

   ```env
   DEFAULT_UPSTREAM_URL=https://llm.kimchi.dev/openai/v1
   OPENMODEL_API_KEY=om-xxxxxxxxxxxxxxxxxxxxxxxxxx
   OPENMODEL_BASE_URL=https://api.openmodel.ai/v1
   PORT=4000
   ADMIN_PASSWORD=your_admin_password
   ROUTER_PASSWORD=your_router_password
   DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
   ```

2. Install dependencies and run with `uv`:

   ```bash
   uv run main.py
   ```

   Or with a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows
   pip install -r requirements.txt  # or install dependencies manually
   uvicorn main:app --host 0.0.0.0 --port 4000
   ```

3. Point Claude Code at it:

   ```bash
   export ANTHROPIC_BASE_URL=http://localhost:4000
   export ANTHROPIC_API_KEY=anything
   ```

4. Use a model:

   ```bash
   claude --model kimi-k2.6
   ```

   Or set it as default:

   ```bash
   claude config set model kimi-k2.6
   ```

5. Open the dashboard:

   ```
   http://localhost:4000/dashboard
   ```

   Login with `ADMIN_USERNAME` (default: `iyanadmin`) and `ADMIN_PASSWORD`.

## Project Structure

```
.
├── main.py              # FastAPI app, routes, SSE broadcaster
├── config.py            # Environment config, key rotation, state
├── translator.py        # Anthropic ↔ OpenAI request/response translation
├── database.py          # asyncpg persistence layer
├── templates/           # Jinja2 dashboard templates
├── static/              # Static assets (CSS, JS)
└── modules/             # Additional modules
```

## Notes

- The old `proxy.py` / `kimchi.py` entrypoints have been replaced by `main.py`.
- SSL certificates are only required when running on a remote VPS.
- On first run, the admin password is hashed with bcrypt. Save the printed hash to `ADMIN_PASSWORD_HASH` for subsequent runs.
