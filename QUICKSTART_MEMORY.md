# Quick Start: Conversation Memory

## 1. Database Migration

Database schema sudah otomatis ter-update saat router start. Tabel dan kolom baru akan dibuat otomatis oleh `setup_tables()` di `database.py`.

Cukup restart router:

```bash
# Stop router jika sedang jalan (Ctrl+C)
# Start ulang
uvicorn app.main:app --reload --port 4000
```

Check logs untuk memastikan migration sukses:
```
INFO: Application startup complete.
```

## 2. Verify Database Schema

```bash
# Connect ke database
psql postgresql://llm_router_user:llm_router_pass_2024@localhost:5432/llm_router

# Check tabel chat_sessions
\d chat_sessions

# Should show columns:
# - id
# - name
# - project_identifier (NEW)
# - api_key_hash (NEW)
# - last_model (NEW)
# - created_at
# - updated_at

# Check indexes
\di idx_sessions_identifier
\di idx_sessions_api_key

# Exit
\q
```

## 3. Test Conversation Memory

### Option A: Using Python Test Script

```bash
# Edit test script, set your API key
nano test_conversation_memory.py
# Ubah: API_KEY = "YOUR_API_KEY_HERE"

# Run test
python3 test_conversation_memory.py
```

Script akan:
- ✓ Kirim first message dengan nama
- ✓ Ask "Siapa nama saya?" (should remember)
- ✓ Ask "Apa yang sedang saya bangun?" (should remember project)
- ✓ Test new session (should NOT remember)
- ✓ Test tanpa memory header (should NOT remember)

### Option B: Manual Test dengan cURL

**Request 1: Set context**
```bash
curl -X POST http://localhost:4000/v1/messages \
  -H "Authorization: Bearer sk-xxx..." \
  -H "Content-Type: application/json" \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: my-test" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [
      {"role": "user", "content": "Halo, nama saya Budi dan saya suka kucing."}
    ],
    "max_tokens": 500
  }'
```

**Request 2: Recall context (tunggu 2 detik)**
```bash
curl -X POST http://localhost:4000/v1/messages \
  -H "Authorization: Bearer sk-xxx..." \
  -H "Content-Type: application/json" \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: my-test" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [
      {"role": "user", "content": "Siapa nama saya dan apa yang saya suka?"}
    ],
    "max_tokens": 500
  }'
```

Expected response: "Nama Anda Budi dan Anda suka kucing."

## 4. Check Logs

Router akan print logs saat conversation memory aktif:

```
[LOG] Conversation Memory: Loaded 2 history messages for session my-test
```

Cari log ini untuk verify history di-load.

## 5. Check Database

```bash
psql postgresql://llm_router_user:llm_router_pass_2024@localhost:5432/llm_router

# List all sessions
SELECT id, name, project_identifier, last_model, updated_at 
FROM chat_sessions 
ORDER BY updated_at DESC 
LIMIT 10;

# Check messages untuk session tertentu (ganti <session_id>)
SELECT id, role, LEFT(content, 100) as content_preview, created_at
FROM chat_messages
WHERE session_id = <session_id>
ORDER BY id ASC;

# Count messages per session
SELECT session_id, COUNT(*) as message_count
FROM chat_messages
GROUP BY session_id
ORDER BY message_count DESC;
```

## 6. Production Deployment

### Update .env di server

```bash
# SSH ke server
ssh user@your-server

# Edit .env
cd /app/llm-router
nano .env

# Add (jika belum ada):
CONVERSATION_MEMORY_ENABLED=true
MAX_HISTORY_MESSAGES=20
MEMORY_RETENTION_DAYS=30
```

### Restart Docker Container

```bash
# Rebuild dan restart
docker-compose down
docker-compose up -d --build

# Check logs
docker-compose logs -f
```

### Verify Production

```bash
# Test dari local ke production
curl -X POST https://routers.iyantama.tech/v1/messages \
  -H "Authorization: Bearer YOUR_PROD_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: prod-test" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [
      {"role": "user", "content": "Test conversation memory production"}
    ],
    "max_tokens": 100
  }'
```

## 7. Monitoring

### Token Usage

Monitor input tokens di dashboard. Dengan conversation memory:
- Normal request: ~100 input tokens
- With 20 history messages: ~2000-5000 input tokens

### Database Size

```sql
-- Check total messages
SELECT COUNT(*) FROM chat_messages;

-- Check database size
SELECT pg_size_pretty(pg_database_size('llm_router'));

-- Check table sizes
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE tablename IN ('chat_sessions', 'chat_messages')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

### Cleanup Old Sessions (Manual)

```sql
-- Dry run: count sessions yang akan dihapus
SELECT COUNT(*) 
FROM chat_sessions 
WHERE updated_at < NOW() - INTERVAL '30 days';

-- Delete old sessions (messages akan auto-delete via CASCADE)
DELETE FROM chat_sessions 
WHERE updated_at < NOW() - INTERVAL '30 days';
```

## Troubleshooting

### History tidak muncul

**Symptom**: AI tidak mengingat percakapan sebelumnya

**Check:**
1. Header `X-Enable-Memory: true` dikirim?
2. Session ID konsisten antar requests?
3. Database contains messages?
   ```sql
   SELECT * FROM chat_sessions WHERE project_identifier = 'your-session-id';
   SELECT * FROM chat_messages WHERE session_id = X;
   ```
4. Server logs menunjukkan "[LOG] Loaded X history messages"?

**Solutions:**
- Pastikan `CONVERSATION_MEMORY_ENABLED=true` di `.env`
- Restart router setelah ubah `.env`
- Check database connection (`DATABASE_URL`)

### Token usage terlalu tinggi

**Symptom**: Input tokens naik drastis

**Solutions:**
1. Kurangi `MAX_HISTORY_MESSAGES` (misal dari 20 ke 10)
2. Gunakan opt-in hanya untuk conversations yang butuh context
3. Start new session untuk percakapan baru (ubah `X-Session-Id`)

### Database error

**Symptom**: `asyncpg.exceptions.UndefinedColumnError: column "project_identifier" does not exist`

**Solutions:**
1. Restart router untuk trigger migration
2. Manual migration:
   ```sql
   ALTER TABLE chat_sessions
   ADD COLUMN IF NOT EXISTS project_identifier VARCHAR(255),
   ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(64),
   ADD COLUMN IF NOT EXISTS last_model VARCHAR(100);
   
   CREATE INDEX IF NOT EXISTS idx_sessions_identifier
   ON chat_sessions(project_identifier);
   
   CREATE INDEX IF NOT EXISTS idx_sessions_api_key
   ON chat_sessions(api_key_hash);
   ```

### Context window exceeded

**Symptom**: Error "context length exceeded" meskipun pakai history limit

**Explanation**: Current request + history masih terlalu besar

**Solutions:**
1. Kurangi `MAX_HISTORY_MESSAGES`
2. Auto-compaction akan trigger (router handle otomatis)
3. Start new session

## Next Steps

1. ✓ Deploy to production
2. ✓ Monitor token usage
3. ✓ Test with real users
4. 📋 Implement auto-cleanup job (future)
5. 📋 Add session management API (future)
6. 📋 Add summary-based approach untuk long conversations (future)

## Documentation

- Full documentation: [CONVERSATION_MEMORY.md](./CONVERSATION_MEMORY.md)
- Test script: [test_conversation_memory.py](./test_conversation_memory.py)
