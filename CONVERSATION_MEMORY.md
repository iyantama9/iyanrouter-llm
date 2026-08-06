# Conversation Memory Feature

## Overview

Conversation Memory memungkinkan LLM Router menyimpan riwayat percakapan di database PostgreSQL, sehingga AI dapat mengingat konteks antar sesi dalam project yang sama. Fitur ini mengatasi masalah context compaction yang terjadi di client side.

## Konfigurasi

Di file `.env`:

```bash
CONVERSATION_MEMORY_ENABLED=true
MAX_HISTORY_MESSAGES=20
MEMORY_RETENTION_DAYS=30
```

- `CONVERSATION_MEMORY_ENABLED`: Enable/disable fitur (default: true)
- `MAX_HISTORY_MESSAGES`: Jumlah maksimal pesan history yang di-inject (default: 20)
- `MEMORY_RETENTION_DAYS`: Berapa hari session disimpan sebelum auto-cleanup (default: 30)

## Cara Menggunakan

### Opt-in Header

Untuk mengaktifkan conversation memory pada request tertentu, tambahkan header:

```
X-Enable-Memory: true
```

### Session Identification

Ada 2 cara untuk mengidentifikasi session:

#### 1. Manual Session ID (Recommended)

Kirim header `X-Session-Id` dengan identifier unik:

```
X-Session-Id: project-xyz-main
```

**Keuntungan:**
- Kontrol penuh atas session
- Bisa punya multiple sessions per API key
- Predictable (session ID yang sama = history yang sama)

**Use case:**
- Per-project sessions: `X-Session-Id: my-app-v2`
- Per-feature sessions: `X-Session-Id: my-app-auth-module`
- Per-branch sessions: `X-Session-Id: my-app-feature-branch`

#### 2. Auto-generated Session ID

Kalau tidak kirim `X-Session-Id`, router akan auto-generate dari:
```
MD5(api_key + first_user_message[:50])[:16]
```

**Keuntungan:**
- Zero configuration
- Otomatis per-conversation

**Kelemahan:**
- Tidak reliable kalau user edit first message
- Sulit untuk intentionally start new session

### Hybrid Approach (Default)

Router menggunakan hybrid:
1. Cek `X-Session-Id` header dulu
2. Kalau tidak ada, auto-generate
3. Simpan mapping (session_identifier + api_key_hash) di database

## Request Example

### Python dengan requests

```python
import requests

url = "https://routers.iyantama.tech/v1/messages"
headers = {
    "Authorization": "Bearer YOUR_API_KEY",
    "Content-Type": "application/json",
    "X-Enable-Memory": "true",
    "X-Session-Id": "my-project-main"  # Optional
}
payload = {
    "model": "claude-sonnet-5",
    "messages": [
        {"role": "user", "content": "Halo, saya sedang build LLM router"}
    ],
    "max_tokens": 1000
}

response = requests.post(url, json=payload, headers=headers)
print(response.json())
```

### cURL

```bash
curl -X POST https://routers.iyantama.tech/v1/messages \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: my-project-main" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [
      {"role": "user", "content": "Halo, nama saya Iyan"}
    ],
    "max_tokens": 1000
  }'
```

Request kedua dengan session yang sama:

```bash
curl -X POST https://routers.iyantama.tech/v1/messages \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Enable-Memory: true" \
  -H "X-Session-Id: my-project-main" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [
      {"role": "user", "content": "Siapa nama saya?"}
    ],
    "max_tokens": 1000
  }'
```

AI akan menjawab "Iyan" karena mengingat percakapan sebelumnya.

## Bagaimana Cara Kerjanya

### 1. Request Masuk
- Router extract `X-Enable-Memory` dan `X-Session-Id` headers
- Kalau `X-Session-Id` tidak ada, auto-generate dari hash(api_key + first_message)

### 2. Load History
- Cari session di database berdasarkan `project_identifier` + `api_key_hash`
- Load N pesan terakhir (default: 20)
- Parse content blocks menjadi text

### 3. Inject History
Di `translator.py`, history di-inject ke messages array:
```python
messages = system_messages + history_messages + current_messages
```

Struktur:
```python
[
    {"role": "system", "content": "..."},           # System prompt
    {"role": "user", "content": "historical msg 1"}, # History
    {"role": "assistant", "content": "response 1"},
    {"role": "user", "content": "historical msg 2"},
    {"role": "assistant", "content": "response 2"},
    {"role": "user", "content": "current question"}  # New message
]
```

### 4. Send to Upstream
Request dengan history dikirim ke upstream provider (Anthropic, OpenAI, dll)

### 5. Save Response
Setelah response datang (streaming atau non-streaming):
- Save user message ke `chat_messages` table
- Save assistant response (JSON format untuk content blocks)
- Update `chat_sessions.updated_at`

## Database Schema

### chat_sessions
```sql
CREATE TABLE chat_sessions (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    project_identifier VARCHAR(255),  -- Session identifier (manual atau auto)
    api_key_hash VARCHAR(64),         -- SHA256(api_key) untuk isolasi
    last_model VARCHAR(100),          -- Model terakhir yang digunakan
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_sessions_identifier ON chat_sessions(project_identifier);
CREATE INDEX idx_sessions_api_key ON chat_sessions(api_key_hash);
```

### chat_messages
```sql
CREATE TABLE chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,        -- 'user' atau 'assistant'
    content TEXT NOT NULL,            -- String untuk user, JSON untuk assistant
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

## Context Window Management

History + current request bisa exceed model context limit. Router menggunakan existing `compact_messages()` function:

- Keep system message always
- Keep last N messages dari history (default: 20)
- Cut at safe boundaries (sebelum user messages)
- Prioritas: Current request > Recent history > Old history

Auto-compaction tetap bekerja seperti biasa jika context window exceeded.

## Token Usage

⚠️ **Perhatian**: Conversation memory akan meningkatkan token usage karena history dikirim setiap request.

Estimasi:
- 20 messages history ≈ 2000-5000 tokens (tergantung panjang messages)
- Dengan MAX_HISTORY_MESSAGES=20, setiap request akan consume +2k-5k input tokens

**Rekomendasi:**
- Gunakan opt-in (tidak enable untuk semua request)
- Set MAX_HISTORY_MESSAGES sesuai kebutuhan (10-20)
- Monitor token usage di dashboard

## Privacy & Security

- **API Key Isolation**: Setiap API key punya sessions terpisah (via SHA256 hash)
- **Project Isolation**: Sessions diidentifikasi per-project via `project_identifier`
- **Auto-cleanup**: Sessions older than `MEMORY_RETENTION_DAYS` otomatis dihapus
- **Opt-in**: Feature disabled by default (butuh explicit header)

**Data yang Disimpan:**
- User messages (text content)
- Assistant responses (JSON content blocks)
- Session metadata (identifier, api_key_hash, timestamps)

**Data yang TIDAK Disimpan:**
- Raw API keys (hanya SHA256 hash)
- System prompts
- Anthropic model parameters (temperature, etc)

## Troubleshooting

### History tidak muncul di response
1. Cek header `X-Enable-Memory: true` sudah dikirim
2. Cek session ID konsisten antar requests
3. Cek database: `SELECT * FROM chat_sessions WHERE project_identifier = 'your-id'`
4. Cek logs di terminal untuk "[LOG] Loaded X history messages"

### Token usage terlalu tinggi
1. Kurangi `MAX_HISTORY_MESSAGES` di `.env`
2. Gunakan opt-in hanya untuk requests yang butuh context
3. Start new session untuk percakapan baru

### Context window exceeded meskipun sudah pakai history limit
1. Current request + history masih terlalu besar
2. Auto-compaction akan trigger (cek logs)
3. Pertimbangkan kurangi `MAX_HISTORY_MESSAGES`
4. Atau start new session

### Session ID collision (dua project pakai ID sama)
- Tidak masalah! Sessions di-isolate per API key via `api_key_hash`
- Session "my-app" dengan API key A ≠ session "my-app" dengan API key B

## Cleanup Old Sessions

Auto-cleanup runs automatically (TODO: implement cleanup job).

Manual cleanup via SQL:

```sql
DELETE FROM chat_sessions 
WHERE updated_at < NOW() - INTERVAL '30 days';
```

## Limitations

1. **Token Cost**: History dikirim setiap request (tidak ada client-side caching)
2. **Latency**: Database query + larger request size
3. **Storage**: All messages stored in database
4. **No Summary**: Menyimpan full messages, bukan summary (bisa ditambahkan nanti)
5. **No Client Control**: Client tidak bisa melihat/edit history (hanya melalui database)

## Future Improvements

- [ ] Auto-cleanup job untuk old sessions
- [ ] Summary-based approach (lebih efficient untuk long conversations)
- [ ] API endpoint untuk list/view/delete sessions
- [ ] Client-side session management UI
- [ ] Token usage warning ketika history terlalu besar
- [ ] Selective history (hanya inject relevant messages, bukan semua)
