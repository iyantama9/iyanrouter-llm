# LLM Router Development Context

**Last Updated:** 2026-08-06  
**Developer:** Iyan (@iyantama9)  
**Production URL:** https://routers.iyantama.tech  
**Server:** iyanserve@70.153.8.223  
**Server Path:** /home/iyanserve/llm-router

## Project Overview

LLM Router adalah proxy/load balancer untuk multiple LLM providers dengan features:
- Multi-provider routing (9 providers total)
- Automatic key rotation
- Rate limit handling
- Fallback mechanisms
- Request logging & statistics
- Admin dashboard
- **Conversation Memory** (NEW - 2026-08-06)

## Tech Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI + Uvicorn
- **Database:** PostgreSQL 16
- **Deployment:** Docker + Docker Compose
- **Server OS:** Ubuntu (Docker containers)
- **Git:** https://github.com/iyantama9/iyanrouter-llm.git

## Providers Configuration

### 1. Kimchi (kc)
- **Keys:** 13 keys loaded
- **Base URL:** https://llm.kimchi.dev/openai/v1
- **Models:** 13 models (qwen3-coder-next-fp8, deepseek-v4-flash, glm-5.2-fp8, kimi-k2.5, etc)
- **Format:** OpenAI-compatible
- **Env:** `CASTAI_API_KEYS` (comma-separated)

### 2. Cavoti (cv)
- **Keys:** 4 keys loaded
- **Base URL:** https://cavoti.com/v1
- **Models:** 8 models (gpt-5.4, gpt-5.5, gpt-5.6-luna/sol/terra, codex-auto-review, etc)
- **API Key:** `CAVOTI_API_KEY` + `CV_API_KEYS` list
- **Format:** OpenAI-compatible

### 3. BluesMinds (bm)
- **Keys:** 2 keys loaded
- **Base URL:** https://api.bluesminds.com/v1
- **Models:** 200+ models including K1/claude-*, nvidia/*, meta/*, qwen/*, deepseek-ai/*
- **Prefix:** Models in router exposed as `bm/<model>`
- **Note:** Very large model catalog
- **Env:** `BLUESMINDS_API_KEY` + `BM_API_KEYS`

### 4. byNara (nry)
- **Keys:** 2 keys loaded
- **Base URL:** https://router.bynara.id/v1
- **Models:** 40+ models (claude-fable-5, deepseek-v4-*, glm-5.2, gpt-5.*, mimo-v2.5-*, etc)
- **Env:** `NARA_BASE_URL`, `NARA_MODELS`, `NR_API_KEYS`

### 5. Dahl (dahl)
- **Keys:** 8 keys loaded
- **Base URL:** https://inference.dahl.global/v1
- **Models:** 3 models (MiniMaxAI/MiniMax-M2.7, moonshotai/Kimi-K2.6, zai-org/GLM-5.2-FP8)
- **Note:** Models use long format names
- **Prefix:** Models resoled via `resolve_dahl_model()` function

### 6. Qwen Cloud (qc)
- **Keys:** 60 keys loaded (most keys!)
- **Base URL:** https://dashscope-intl.aliyuncs.com/compatible-mode/v1
- **Models:** 149 models (qwen3.7-max, deepseek-v4-*, glm-5.2, kimi-k2.7-code, etc)
- **Special:** Image generation via different endpoint
- **Fallback Order:** qwen3.7-max → qwen-max → qwen-plus → deepseek-v3.2 → glm-5.2 → kimi-k2.7-code → qwen-turbo
- **Per-Model Key Tracking:** Each key tracks exhaustion per model
- **Env:** `QWEN_CLOUD_BASE_URL`, `QWEN_CLOUD_MODELS`, `QC_API_KEYS`, `QC_FALLBACK_ORDER`

### 7. MarketKu (marketku)
- **Keys:** 1 key loaded
- **Base URL:** https://router.marketku.id/v1
- **Models:** 14 models (auto, auto-thinking, deepseek-3.2, glm-5, haiku-4.5*, mimo-v2.5-pro, sonnet-4.5*, etc)
- **Known Issue:** mimo-v2.5-pro requires tool definitions in every request with tool_result
- **Env:** `MARKETKU_BASE_URL`, `MARKETKU_MODELS`, `MARKETKU_API_KEYS`

### 8. Atomesus (atomesus)
- **Keys:** 9 keys loaded
- **Base URL:** https://api.atomesus.com/v1
- **Models:** 1 model (cipher)
- **Env:** `ATOMESUS_BASE_URL`, `ATOMESUS_MODELS`, `ATOMESUS_API_KEYS`

### 9. Weize (weize)
- **Keys:** 1 key loaded
- **Base URL:** https://weizerouter.web.id/v1
- **Models:** 60+ models (grok-4.5, glm-5.*, kimi-k3, mimo-v2.5-*, minimax-m3, deepseek-v4-*, claude-*, gemini-*, etc)
- **Prefix:** Models di `/v1/models` endpoint show as `weize/<model>`
- **Recent Addition:** Added 2026-08-05
- **Env:** `WEIZE_BASE_URL`, `WEIZE_MODELS`, `WEIZE_API_KEYS`

## Database Schema

**Database URL:** `postgresql://llm_router_user:llm_router_pass_2024@localhost:5432/llm_router`

### Tables

#### api_keys
```sql
- id: SERIAL PRIMARY KEY
- key_value: TEXT (encrypted key)
- key_prefix: TEXT (first 10 chars for logs)
- status: VARCHAR(20) DEFAULT 'Standby'
- provider: VARCHAR(20) DEFAULT 'kc'
- created_at: TIMESTAMP
```

#### request_logs
```sql
- id: SERIAL PRIMARY KEY
- model: VARCHAR(100)
- status_code: INTEGER
- key_prefix: TEXT
- rotated: BOOLEAN
- latency_ms: INTEGER
- input_tokens: INTEGER DEFAULT 0
- output_tokens: INTEGER DEFAULT 0
- created_at: TIMESTAMP
Indexes: idx_logs_created_at, idx_logs_model, idx_logs_key_prefix
```

#### chat_sessions (NEW - Conversation Memory)
```sql
- id: SERIAL PRIMARY KEY
- name: VARCHAR(255)
- project_identifier: VARCHAR(255)  -- Session ID from X-Session-Id header
- api_key_hash: VARCHAR(64)         -- SHA256(api_key) for isolation
- last_model: VARCHAR(100)
- created_at: TIMESTAMP
- updated_at: TIMESTAMP
Indexes: idx_sessions_identifier, idx_sessions_api_key
```

#### chat_messages (NEW - Conversation Memory)
```sql
- id: SERIAL PRIMARY KEY
- session_id: INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE
- role: VARCHAR(50)      -- 'user' or 'assistant'
- content: TEXT          -- String for user, JSON for assistant
- created_at: TIMESTAMP
```

## Key Features

### 1. Automatic Key Rotation
- Per-provider rotation on 401/402/403/429 errors
- Qwen Cloud: Per-model key tracking (key exhausted for specific model only)
- Fallback to different models when all keys exhausted

### 2. Context Window Auto-Compaction
- 3 levels: None → 20 messages → 6 messages
- Triggers on context_length_exceeded errors
- Function: `compact_messages()` in translator.py

### 3. Conversation Memory (NEW - 2026-08-06)
**Files:**
- `app/database.py`: Schema + helper functions
- `app/routers/proxy.py`: Session identification + persistence
- `app/translator.py`: History injection
- `app/config.py`: Config variables

**How it works:**
- Client sends `X-Enable-Memory: true` + `X-Session-Id: <id>` headers
- Router loads last 20 messages from database
- Injects: [system] + [history] + [current messages]
- Saves user message + AI response after completion

**Configuration (.env):**
```bash
CONVERSATION_MEMORY_ENABLED=true
MAX_HISTORY_MESSAGES=20
MEMORY_RETENTION_DAYS=30
```

**Usage Example:**
```bash
curl -X POST https://routers.iyantama.tech/v1/messages \
  -H "Authorization: Bearer <api-key>" \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: my-project" \
  -d '{"model":"gpt-5.5","messages":[...]}'
```

### 4. Admin Dashboard
- **URL:** /admin (password protected)
- **Features:** Request logs, API key status, provider stats
- **Password:** Set via `ADMIN_PASSWORD` in .env

### 5. SSE Real-time Updates
- Endpoint: `/api/sse`
- Broadcasts: request logs, status updates
- Used by admin dashboard for live updates

## File Structure

```
llm-router/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── config.py            # Config + key rotation logic
│   ├── database.py          # PostgreSQL operations
│   ├── translator.py        # Format conversion (Anthropic ↔ OpenAI)
│   ├── sse.py               # Server-Sent Events broadcaster
│   └── routers/
│       ├── admin.py         # Admin dashboard endpoints
│       ├── proxy.py         # Main proxy logic (most complex file)
│       └── playground.py    # Playground UI endpoints
├── static/                  # Admin dashboard HTML/CSS/JS
├── .env                     # Environment variables (gitignored)
├── .env.example             # Example .env template
├── Dockerfile               # Container build
├── docker-compose.yml       # Multi-container orchestration
├── CONVERSATION_MEMORY.md   # Full conversation memory docs
├── QUICKSTART_MEMORY.md     # Deployment guide
└── test_conversation_memory.py  # Automated test script
```

## Recent Changes

### 2026-08-06: Conversation Memory Feature
- ✅ Extended database schema (project_identifier, api_key_hash, last_model)
- ✅ Session identification (hybrid: explicit or auto-generated)
- ✅ History loading + injection
- ✅ Response persistence (streaming + non-streaming)
- ✅ Configuration via .env
- ✅ Deployed to production
- ✅ Tested successfully

### 2026-08-05: Weize Provider
- ✅ Added Weize provider support
- ✅ 60+ models available
- ✅ Models prefixed as `weize/<model>` in /v1/models

### 2026-08-04: BluesMinds Model List
- ✅ Added 200+ BluesMinds models to /v1/models endpoint
- ✅ Fixed model prefix handling

### Previous: MarketKu Tags Fix
- Fixed tool definition requirement for mimo-v2.5-pro
- Improved error handling for tool_result messages

## Known Issues

### 1. ROUTER_PASSWORD Authentication (DISABLED)
**Issue:** Router password check blocks legitimate upstream API keys
**Location:** `app/routers/proxy.py` - `_check_router_auth()` function
**Current State:** `ROUTER_PASSWORD` commented out in production .env
**Impact:** Router authentication disabled for now
**TODO:** Implement proper auth that doesn't block upstream keys

### 2. MarketKu mimo-v2.5-pro Tool Requirement
**Issue:** Model requires tool definitions in every request that contains tool_result
**Workaround:** Always send tool definitions with tool_result messages
**Impact:** Cannot use tool_result without redeclaring tools
**Status:** Known upstream API limitation

### 3. Context Window Error Parsing
**Issue:** Different providers return different error formats
**Current:** Regex-based detection in `is_context_window_error()`
**Impact:** May miss some error variants
**Status:** Working but could be improved

## Deployment

### Development (Local)
```bash
# Install dependencies
pip install -r requirements.txt

# Run with hot reload
uvicorn app.main:app --reload --port 4000
```

### Production (Docker)
```bash
# SSH to server
ssh iyanserve@70.153.8.223

# Navigate to project
cd /home/iyanserve/llm-router

# Update .env if needed
nano .env

# Rebuild and restart
docker compose down
docker compose up -d --build

# Check logs
docker logs --tail 50 llm-router-app

# Check containers
docker ps | grep llm-router
```

### Docker Containers
- **llm-router-app:** Main application (port 4000)
- **llm-router-db:** PostgreSQL database (internal port 5432)

## Endpoints

### Public API
- `POST /v1/messages` - Main proxy endpoint (Anthropic format)
- `POST /v1/chat/completions` - OpenAI format endpoint
- `GET /v1/models` - List all available models
- `GET /models` - Alias for /v1/models

### Admin Dashboard
- `GET /admin` - Dashboard UI (password protected)
- `GET /api/status` - Router status + stats
- `GET /api/logs` - Request logs (paginated)
- `GET /api/sse` - Real-time SSE stream

### Playground (TODO)
- `GET /playground` - Chat UI
- Session management endpoints

## Configuration Variables

See `.env.example` for full list. Key variables:

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:port/db

# Providers (comma-separated keys)
CASTAI_API_KEYS=key1,key2,key3
CV_API_KEYS=key1,key2
BM_API_KEYS=key1,key2
# ... etc for all providers

# Security
ADMIN_PASSWORD=your-admin-password
ROUTER_PASSWORD=disabled-for-now  # Currently commented out

# Conversation Memory
CONVERSATION_MEMORY_ENABLED=true
MAX_HISTORY_MESSAGES=20
MEMORY_RETENTION_DAYS=30

# Monitoring
SLOW_RESPONSE_THRESHOLD_MS=5000
```

## Testing

### Manual Test (cURL)
```bash
# Simple request
curl -X POST https://routers.iyantama.tech/v1/messages \
  -H "Authorization: Bearer sk-f6fc5625..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.5",
    "messages": [{"role": "user", "content": "test"}],
    "max_tokens": 50
  }'

# With conversation memory
curl -X POST https://routers.iyantama.tech/v1/messages \
  -H "Authorization: Bearer sk-f6fc5625..." \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: test-session" \
  -d '{"model":"gpt-5.5","messages":[...]}'
```

### Automated Test
```bash
# Edit test script first, set API key
nano test_conversation_memory.py

# Run test
python3 test_conversation_memory.py
```

## Troubleshooting

### Router Not Responding
```bash
# Check container status
ssh iyanserve@70.153.8.223 "docker ps | grep llm-router"

# Check logs
ssh iyanserve@70.153.8.223 "docker logs --tail 100 llm-router-app"

# Restart if needed
ssh iyanserve@70.153.8.223 "docker restart llm-router-app"
```

### Database Issues
```bash
# Connect to database
ssh iyanserve@70.153.8.223
docker exec -it llm-router-db psql -U llm_router_user -d llm_router

# Check tables
\dt

# Check session count
SELECT COUNT(*) FROM chat_sessions;

# Check recent messages
SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT 10;
```

### High Token Usage
- Check if conversation memory is being used unnecessarily
- Verify MAX_HISTORY_MESSAGES setting
- Consider starting new sessions more frequently

## Statistics (as of 2026-08-06)

- **Total Requests:** 7,070+
- **Total Failovers:** 806
- **Active Keys:** 100 (across 9 providers)
- **Models Available:** 300+
- **Uptime:** 11 days (current container)

## Future Improvements

- [ ] Fix ROUTER_PASSWORD authentication logic
- [ ] Auto-cleanup job for old chat sessions
- [ ] Summary-based conversation memory (more efficient)
- [ ] Session management API endpoints
- [ ] Token usage alerts/monitoring
- [ ] Per-provider usage analytics
- [ ] Rate limit prediction
- [ ] Cost tracking per API key
