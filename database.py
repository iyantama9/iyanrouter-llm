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
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    await execute("""
        CREATE TABLE IF NOT EXISTS server_config (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT
        )
    """)
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
    for k in ("total_requests", "failover_count", "start_time"):
        await execute("""
            INSERT INTO server_config (key, value) VALUES ($1, '0')
            ON CONFLICT (key) DO NOTHING
        """, k)
