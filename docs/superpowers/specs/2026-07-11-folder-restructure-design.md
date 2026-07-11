# Folder Restructure Design — IyanRouter LLM

## Goal

Make the project easier to understand and maintain by grouping files by responsibility. The refactor must be purely organizational: no behavior changes, no new features.

## Current Problems

- Root directory mixes application code, scratch scripts, and planning docs.
- `main.py` is large and contains proxy, admin dashboard, and playground routes together.
- Scratch scripts (`scratch_*.py`) clutter the root and are only used for testing.
- `BUGS.md` and `plan-dashboard-iyanrouters.md` have no dedicated home.

## Target Structure

```
llm-router/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI bootstrap + lifespan only
│   ├── config.py             # Environment config + global state
│   ├── database.py           # asyncpg persistence layer
│   ├── translator.py         # Anthropic ↔ OpenAI translation
│   ├── sse.py                # SSE broadcaster
│   └── routers/
│       ├── __init__.py
│       ├── proxy.py          # Anthropic-compatible proxy (/v1/messages, /models)
│       ├── admin.py          # Dashboard HTML routes + admin API
│       └── playground.py     # Playground chat + session API
├── templates/                # Jinja2 templates (unchanged)
├── static/                   # Reserved for future local CSS/JS assets
├── docs/
│   ├── BUGS.md
│   └── plan-dashboard-iyanrouters.md
├── scratch/                  # (to be removed — scripts were one-off tests)
├── README.md
├── .env.example
├── .gitignore
└── settings.json
```

## Design Decisions

1. **Pure move/split** — no logic changes. All function signatures, env vars, and routes stay the same.
2. **`main.py` keeps only bootstrap** — creates the FastAPI app, mounts static files, includes routers, and defines the lifespan context manager.
3. **Routers split by user-facing concern**:
   - `proxy.py`: external Anthropic-compatible API used by Claude Code.
   - `admin.py`: dashboard UI and key/status management API.
   - `playground.py`: chat playground sessions and messages.
4. **Shared helpers stay in `app/` root** — `config.py`, `database.py`, `translator.py`, and `sse.py` are imported by routers.
5. **Delete scratch files** — user confirmed they are only for testing.
6. **Docs and plans move to `docs/`** — keeps root clean.
7. **Create empty `static/` directory** — `main.py` currently references `StaticFiles(directory="static")`; an empty dir prevents runtime errors.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Circular imports | Routers import from `app.config`, `app.database`, etc. — never the reverse. `main.py` imports routers last. |
| Import paths break | Update all internal imports to be relative or absolute from package root. |
| Static directory missing | Create empty `static/` directory. |
| App fails to start | Run `python -c "import app.main"` and `uvicorn app.main:app --reload` to verify. |

## Verification

- `python -c "from app.main import app; print('import ok')"`
- `python -m uvicorn app.main:app --host 0.0.0.0 --port 4000` starts without error.
- Existing dashboard login and proxy `/v1/models` remain reachable.

## Out of Scope

- No code style changes beyond import path fixes.
- No new tests.
- No CI/CD changes.
- No renaming of `templates/`.
