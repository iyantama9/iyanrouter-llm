import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool = None

async def init_db():
    import asyncpg
    global _pool
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await setup_tables()

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

async def execute(query, *args):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        return await conn.execute(query, *args)

async def fetch(query, *args):
    if not _pool:
        return []
    async with _pool.acquire() as conn:
        return await conn.fetch(query, *args)

async def fetchrow(query, *args):
    if not _pool:
        return None
    async with _pool.acquire() as conn:
        return await conn.fetchrow(query, *args)

async def setup_tables():
    await execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id SERIAL PRIMARY KEY,
            key_value TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'Standby',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        CREATE TABLE IF NOT EXISTS request_logs (
            id SERIAL PRIMARY KEY,
            model VARCHAR(100),
            status_code INTEGER,
            key_prefix TEXT,
            rotated BOOLEAN DEFAULT FALSE,
            latency_ms INTEGER,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        CREATE TABLE IF NOT EXISTS server_config (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT
        )
    """)
    # ── Migration: add token columns if upgrading from older schema ──
    await execute("""
        ALTER TABLE request_logs
        ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0
    """)
    # ── Migration: add provider column to api_keys ──
    await execute("""
        ALTER TABLE api_keys
        ADD COLUMN IF NOT EXISTS provider VARCHAR(20) DEFAULT 'kc'
    """)

    # Manually fix known existing Cavoti keys from the user
    await execute("""
        UPDATE api_keys SET provider = 'cv' WHERE key_value IN ('sk-FuSN7hE19o550dD7Y9QNYNq3DnSSPoASgRDNceTSR3k4hiCh', 'sk-PWELVxFWEgt32MSb3HD9lla6DQuJexpg48cphQ1BBo9u8Aw8');
    """)
    # (sk- keys added as Cavoti will be updated by the new Python logic or manually)
    # Add index for performance on pagination/sorting
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_created_at ON request_logs(created_at DESC)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_model ON request_logs(model)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_key_prefix ON request_logs(key_prefix)
    """)
    # ── Playground Tables ──
    await execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

# ── Chat Session Helpers ──
async def get_chat_sessions():
    return await fetch("SELECT * FROM chat_sessions ORDER BY updated_at DESC")

async def get_chat_session(session_id: int):
    return await fetchrow("SELECT * FROM chat_sessions WHERE id = $1", session_id)

async def create_chat_session(name: str):
    return await fetchrow("INSERT INTO chat_sessions (name) VALUES ($1) RETURNING *", name)

async def update_chat_session(session_id: int, name: str):
    return await fetchrow("UPDATE chat_sessions SET name = $1, updated_at = NOW() WHERE id = $2 RETURNING *", name, session_id)

async def delete_chat_session(session_id: int):
    await execute("DELETE FROM chat_sessions WHERE id = $1", session_id)

async def get_chat_messages(session_id: int):
    return await fetch("SELECT * FROM chat_messages WHERE session_id = $1 ORDER BY id ASC", session_id)

async def save_chat_message(session_id: int, role: str, content: str):
    await execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1", session_id)
    return await fetchrow("INSERT INTO chat_messages (session_id, role, content) VALUES ($1, $2, $3) RETURNING *", session_id, role, content)

    for k in ("total_requests", "failover_count", "start_time"):
        await execute("""
            INSERT INTO server_config (key, value) VALUES ($1, '0')
            ON CONFLICT (key) DO NOTHING
        """, k)
