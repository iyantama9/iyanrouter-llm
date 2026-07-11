# Known Bugs — Kimchi Server

Tracked bugs found in `kimchi-server/`. Check off when fixed, update status as-needed.

## Active Bugs

| # | Bug | Severity | File | Status | Notes |
|---|---|---|---|---|---|
| ☐ | `UndefinedColumnError: column "input_tokens" of relation "request_logs" does not exist` | Medium | `database.py`, `config.py` | **Unfixed** | `ALTER TABLE` migration for `input_tokens` / `output_tokens` needed. Background task `_bg()` fails silently — app doesnt crash, but DB persist fails so counters reset on restart. |
| ☐ | `total_requests` binding goes stale after module import | Low | `kimchi.py` | **Fixed** via `config_module` | Was a Python import trap (`int` immutable → by-value copy). Now reads `config_module.total_requests` live. |

## Recently Fixed

| # | Bug | Severity | File | Status | Notes |
|---|---|---|---|---|---|
| ☑ | Monolithic inline HTML (1,500+ lines in `kimchi.py`) | Low | `kimchi.py` | **Fixed** | Refactored to Jinja2 templates: `templates/base.html`, `login.html`, `dashboard.html` |
| ☑ | Hardcoded admin credentials | High | `config.py` | **Fixed** | Now uses `bcrypt.hashpw()` / `verify_admin_password()` |
| ☑ | Static directory not created on deploy | Medium | `kimchi.py` | **Fixed** | Added `mkdir static/` on remote; `StaticFiles` mount now works |
| ☐ | `TemplateResponse` signature mismatch (potential) | Low | `kimchi.py` | **Needs verify** | Remote may have older FastAPI/Starlette — if `name=/kwarg` order wrong, can crash. Watch logs. |
| ☐ | SSE `log` broadcast sends `{}` on rotation before any log exists | Low | `kimchi.py` | **Needs verify** | `recent_requests[0]` may be empty list — check if dashboard receives `{}` blank entries |

## Tech Debt / Improvements Backlog

| # | Item | Priority | Status | Notes |
|---|---|---|---|---|
| ☐ | DB migration runner (`alembic` or manual) | Medium | **Pending** | Schema changes need `ALTER TABLE ADD COLUMN IF NOT EXISTS` now, but manual only |
| ☐ | Add index on `request_logs(created_at DESC, input_tokens)` | Low | **Pending** | For token pagination / sorting |
| ☐ | Fallback `_bg()` on schema mismatch → retry without token columns | Low | **Pending** | Quick fix for breaking schema changes without migration |

---
*Last updated: 2026-06-18*
*Process: `add_request_log` fails DB write silently → counters not persisted. Restart resets `total_requests`/`failover_count` to last known DB value.*
