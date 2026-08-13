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

async def fetch_one(query, *args):
    return await fetchrow(query, *args)

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

    # Provider column was added in a prior migration; new keys get provider set at insert time.
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
    # ── Conversation Memory Extensions ──
    await execute("""
        ALTER TABLE chat_sessions
        ADD COLUMN IF NOT EXISTS project_identifier VARCHAR(255),
        ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(64),
        ADD COLUMN IF NOT EXISTS last_model VARCHAR(100)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_identifier
        ON chat_sessions(project_identifier)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_api_key
        ON chat_sessions(api_key_hash)
    """)

    # ── Brain Tables ──
    await execute("""
        CREATE TABLE IF NOT EXISTS brain_conversations (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            api_key_hash VARCHAR(64) NOT NULL,
            message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
            role VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            embedding JSONB,
            model VARCHAR(100),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'brain_conversations'
                  AND column_name = 'embedding'
                  AND data_type = 'bytea'
            ) THEN
                ALTER TABLE brain_conversations
                ALTER COLUMN embedding TYPE JSONB
                USING CASE
                    WHEN embedding IS NULL THEN NULL
                    ELSE convert_from(embedding, 'UTF8')::JSONB
                END;
            END IF;
        END $$
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_conversations_session
        ON brain_conversations(session_id)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_conversations_api_key
        ON brain_conversations(api_key_hash)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_conversations_created
        ON brain_conversations(created_at DESC)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_conversations_session_created
        ON brain_conversations(session_id, created_at)
    """)
    await execute("""
        DROP INDEX IF EXISTS idx_brain_conversations_embedding
    """)

    await execute("""
        CREATE TABLE IF NOT EXISTS brain_decisions (
            id SERIAL PRIMARY KEY,
            api_key_hash VARCHAR(64) NOT NULL,
            session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
            decision_type VARCHAR(100),
            title TEXT NOT NULL,
            description TEXT,
            context TEXT,
            outcome TEXT,
            model_ref VARCHAR(150),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        ALTER TABLE brain_decisions ADD COLUMN IF NOT EXISTS model_ref VARCHAR(150)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_decisions_api_key
        ON brain_decisions(api_key_hash)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_decisions_model_feedback
        ON brain_decisions(api_key_hash, decision_type, outcome)
        WHERE decision_type = 'model_feedback'
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_decisions_session
        ON brain_decisions(session_id)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_decisions_created
        ON brain_decisions(created_at DESC)
    """)

    await execute("""
        CREATE TABLE IF NOT EXISTS brain_facts (
            id SERIAL PRIMARY KEY,
            api_key_hash VARCHAR(64) NOT NULL,
            session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
            category VARCHAR(100),
            fact TEXT NOT NULL,
            source TEXT,
            confidence FLOAT DEFAULT 1.0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_facts_api_key
        ON brain_facts(api_key_hash)
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_facts_category
        ON brain_facts(category)
    """)

    await execute("""
        CREATE TABLE IF NOT EXISTS brain_profiles (
            id SERIAL PRIMARY KEY,
            api_key_hash VARCHAR(64) NOT NULL UNIQUE,
            profile_data JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_brain_profiles_api_key
        ON brain_profiles(api_key_hash)
    """)

    # ── Router API Keys ──
    await execute("""
        CREATE TABLE IF NOT EXISTS router_api_keys (
            id SERIAL PRIMARY KEY,
            key_value TEXT NOT NULL UNIQUE,
            key_name TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            last_used_at TIMESTAMP WITH TIME ZONE,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    await execute("""
        CREATE INDEX IF NOT EXISTS idx_router_api_keys_value
        ON router_api_keys(key_value) WHERE is_active = TRUE
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

# ── Conversation Memory Helpers ──
async def get_or_create_session(identifier: str, api_key_hash: str, model: str = None):
    """Get existing session by identifier or create new one."""
    # Try to find existing session
    session = await fetchrow(
        "SELECT id FROM chat_sessions WHERE project_identifier = $1 AND api_key_hash = $2",
        identifier, api_key_hash
    )
    if session:
        # Update last seen
        if model:
            await execute(
                "UPDATE chat_sessions SET updated_at = NOW(), last_model = $1 WHERE id = $2",
                model, session["id"]
            )
        else:
            await execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1", session["id"])
        return session["id"]

    # Create new session
    name = f"Session {identifier[:8]}"
    new_session = await fetchrow(
        "INSERT INTO chat_sessions (name, project_identifier, api_key_hash, last_model) VALUES ($1, $2, $3, $4) RETURNING id",
        name, identifier, api_key_hash, model
    )
    return new_session["id"]

async def load_session_history(session_id: int, limit: int = 20):
    """Load N most recent messages from a session in chronological order."""
    messages = await fetch(
        "SELECT role, content FROM chat_messages WHERE session_id = $1 ORDER BY id DESC LIMIT $2",
        session_id, limit
    )
    # Reverse to get chronological order (oldest first)
    return list(reversed(messages))

async def append_to_session(session_id: int, role: str, content: str):
    """Append a message to session history and return its database row."""
    await execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1", session_id)
    return await fetchrow(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ($1, $2, $3) RETURNING *",
        session_id, role, content
    )

async def cleanup_old_sessions(retention_days: int = 30):
    """Delete sessions older than retention_days."""
    await execute(
        "DELETE FROM chat_sessions WHERE updated_at < NOW() - INTERVAL '%s days'",
        retention_days
    )

# ── Router API Key Helpers ──
async def create_router_api_key(key_name: str):
    """Generate a new router API key."""
    import secrets
    key_value = f"rtr_{secrets.token_urlsafe(32)}"
    return await fetchrow(
        "INSERT INTO router_api_keys (key_value, key_name) VALUES ($1, $2) RETURNING *",
        key_value, key_name
    )

async def get_router_api_keys():
    """Get all active router API keys."""
    return await fetch(
        "SELECT id, key_name, created_at, last_used_at, is_active FROM router_api_keys ORDER BY created_at DESC"
    )

async def verify_router_api_key(key_value: str):
    """Verify router API key and update last_used_at."""
    key = await fetchrow(
        "SELECT id FROM router_api_keys WHERE key_value = $1 AND is_active = TRUE",
        key_value
    )
    if key:
        await execute(
            "UPDATE router_api_keys SET last_used_at = NOW() WHERE id = $1",
            key["id"]
        )
        return True
    return False

async def delete_router_api_key(key_id: int):
    """Delete a router API key."""
    await execute("DELETE FROM router_api_keys WHERE id = $1", key_id)
