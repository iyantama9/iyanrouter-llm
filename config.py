import os
import time
import asyncio
import hashlib
import bcrypt
from dotenv import load_dotenv
from database import execute as db_execute, fetch as db_fetch, fetchrow as db_fetchrow

load_dotenv()

KIMCHI_BASE_URL_RAW = os.getenv("KIMCHI_BASE_URL")
PORT_STR = os.getenv("PORT")
SSL_KEYFILE = os.getenv("SSL_KEYFILE")
SSL_CERTFILE = os.getenv("SSL_CERTFILE")

if not KIMCHI_BASE_URL_RAW:
    raise ValueError("KIMCHI_BASE_URL environment variable is not set")

KIMCHI_BASE_URL = KIMCHI_BASE_URL_RAW.rstrip("/")
SHOW_REASONING = os.getenv("SHOW_REASONING", "false").lower() == "true"

if not PORT_STR:
    raise ValueError("PORT environment variable is not set")

PORT = int(PORT_STR)

# Admin credentials - hash if plaintext, or load hash from env
_ADMIN_HASH = os.getenv("ADMIN_PASSWORD_HASH")
if _ADMIN_HASH:
    # Hash already exists in env
    ADMIN_PASSWORD_HASH = _ADMIN_HASH.encode()
else:
    # Default plaintext password — hash it at runtime (first time only, print to save)
    _raw_password = os.getenv("ADMIN_PASSWORD", "akusukanasigoreng")
    ADMIN_PASSWORD_HASH = bcrypt.hashpw(_raw_password.encode(), bcrypt.gensalt())
    _bcrypt_hash_str = ADMIN_PASSWORD_HASH.decode()
    print(f"[SETUP] Generated bcrypt hash. Save this to ADMIN_PASSWORD_HASH env var:\n{_bcrypt_hash_str}")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "iyanadmin")

# Session secret: stable across restarts (derived from admin creds + salt)
SESSION_SECRET = hashlib.sha256(f"{ADMIN_USERNAME}:{ADMIN_PASSWORD_HASH.decode()}:kimchi-secret-v2".encode()).hexdigest()

# In-memory state (primary for fast access, DB is persistence)
API_KEYS = []
key_statuses = {}
total_requests = 0
failover_count = 0
recent_requests = []
START_TIME = time.time()
current_key_index = 0


def _bg(coro):
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        pass


async def init_state_from_db():
    global API_KEYS, key_statuses, total_requests, failover_count, current_key_index, START_TIME

    API_KEYS.clear()
    key_statuses.clear()

    # Load keys from DB
    rows = await db_fetch("SELECT key_value, key_prefix, status FROM api_keys ORDER BY id")
    if rows:
        for r in rows:
            API_KEYS.append(r["key_value"])
            key_statuses[r["key_value"]] = r["status"]
    else:
        # Seed from .env fallback
        raw_keys = os.getenv("CASTAI_API_KEYS", "")
        env_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not env_keys:
            single_key = os.getenv("CASTAI_API_KEY")
            if single_key:
                env_keys = [single_key]
            else:
                raise ValueError("CASTAI_API_KEY or CASTAI_API_KEYS environment variable is not set")
        for i, k in enumerate(env_keys):
            API_KEYS.append(k)
            key_statuses[k] = "Active" if i == 0 else "Standby"
            prefix = k[:15] + "..." if len(k) > 15 else k
            await db_execute(
                "INSERT INTO api_keys (key_value, key_prefix, status) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                k, prefix, key_statuses[k]
            )

    # Fix active key index
    for i, k in enumerate(API_KEYS):
        if key_statuses.get(k) == "Active":
            current_key_index = i
            break
    else:
        if API_KEYS:
            current_key_index = 0
            key_statuses[API_KEYS[0]] = "Active"
            await db_execute(
                "UPDATE api_keys SET status = 'Active' WHERE key_value = $1",
                API_KEYS[0]
            )

    # Load stats from DB
    tr = await db_fetchrow("SELECT value FROM server_config WHERE key = 'total_requests'")
    if tr:
        total_requests = int(tr["value"])
    fc = await db_fetchrow("SELECT value FROM server_config WHERE key = 'failover_count'")
    if fc:
        failover_count = int(fc["value"])
    st = await db_fetchrow("SELECT value FROM server_config WHERE key = 'start_time'")
    if st and st["value"] and st["value"] != "0":
        START_TIME = float(st["value"])
    else:
        START_TIME = time.time()
        await db_execute(
            "INSERT INTO server_config (key, value) VALUES ('start_time', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            str(START_TIME)
        )

    # Recent requests from DB (last 20)
    logs = await db_fetch("SELECT model, status_code, key_prefix, rotated, latency_ms, created_at FROM request_logs ORDER BY created_at DESC LIMIT 20")
    recent_requests.clear()
    for r in logs:
        ts = r["created_at"]
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%H:%M:%S")
        else:
            ts = str(ts)[11:19]
        recent_requests.append({
            "timestamp": ts,
            "model": r["model"],
            "status_code": r["status_code"],
            "key_used": r["key_prefix"],
            "rotated": r["rotated"],
            "latency_ms": r["latency_ms"]
        })

    print(f"[INIT] Loaded {len(API_KEYS)} keys, {total_requests} total requests, {failover_count} failovers from DB")


def get_current_key():
    if not API_KEYS:
        return ""
    return API_KEYS[current_key_index]


def rotate_key():
    global current_key_index, failover_count
    if len(API_KEYS) <= 1:
        return get_current_key()
    old_key = get_current_key()
    key_statuses[old_key] = "Limited"
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    new_key = get_current_key()
    key_statuses[new_key] = "Active"
    failover_count += 1
    print(f"[LOG] Rotated to key index {current_key_index}: {new_key[:15]}...")
    # Save to DB
    _bg(db_execute("UPDATE api_keys SET status = 'Limited' WHERE key_value = $1", old_key))
    _bg(db_execute("UPDATE api_keys SET status = 'Active' WHERE key_value = $1", new_key))
    _bg(db_execute("INSERT INTO server_config (key, value) VALUES ('failover_count', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", str(failover_count)))
    return new_key


def add_request_log(model, status_code, key_used, rotated, latency_ms):
    global total_requests
    total_requests += 1
    timestamp = time.strftime("%H:%M:%S")
    log_item = {
        "timestamp": timestamp,
        "model": model,
        "status_code": status_code,
        "key_used": key_used[:15] + "...",
        "rotated": rotated,
        "latency_ms": latency_ms
    }
    recent_requests.insert(0, log_item)
    if len(recent_requests) > 20:
        recent_requests.pop()
    # Persist to DB
    _bg(db_execute(
        "INSERT INTO request_logs (model, status_code, key_prefix, rotated, latency_ms) VALUES ($1, $2, $3, $4, $5)",
        model, status_code, log_item["key_used"], rotated, latency_ms
    ))
    _bg(db_execute(
        "INSERT INTO server_config (key, value) VALUES ('total_requests', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        str(total_requests)
    ))


def add_api_key(new_key: str):
    global API_KEYS
    new_key = new_key.strip()
    if not new_key:
        return False, "Key cannot be empty"
    if new_key in API_KEYS:
        return False, "Key already exists"
    API_KEYS.append(new_key)
    key_statuses[new_key] = "Standby"
    prefix = new_key[:15] + "..." if len(new_key) > 15 else new_key
    # Save to DB
    _bg(db_execute(
        "INSERT INTO api_keys (key_value, key_prefix, status) VALUES ($1, $2, 'Standby')",
        new_key, prefix
    ))
    # Also keep .env synced as backup
    _save_keys_to_env()
    return True, "Key added successfully"


def remove_api_key(key_prefix: str):
    global API_KEYS, current_key_index
    target_key = None
    for key in API_KEYS:
        if key.startswith(key_prefix):
            target_key = key
            break
    if not target_key:
        return False, "Key not found"
    if len(API_KEYS) <= 1:
        return False, "Cannot delete the last remaining key"
    active_key = get_current_key()
    if target_key == active_key:
        rotate_key()
    API_KEYS.remove(target_key)
    if target_key in key_statuses:
        del key_statuses[target_key]
    # Fix index
    try:
        current_key_index = API_KEYS.index(get_current_key()) if get_current_key() in API_KEYS else 0
    except Exception:
        current_key_index = 0
    # Remove from DB
    _bg(db_execute("DELETE FROM api_keys WHERE key_value = $1", target_key))
    _save_keys_to_env()
    return True, "Key removed successfully"


def reset_key_status(key_prefix: str):
    for key in API_KEYS:
        if key.startswith(key_prefix):
            key_statuses[key] = "Standby"
            _bg(db_execute("UPDATE api_keys SET status = 'Standby' WHERE key_value = $1", key))
            return True, "Key status reset to Standby"
    return False, "Key not found"


def get_masked_keys():
    result = []
    for idx, key in enumerate(API_KEYS):
        status = key_statuses.get(key, "Standby")
        masked = key[:15] + "..." if len(key) > 15 else key
        result.append({
            "index": idx,
            "prefix": key[:15],
            "masked": masked,
            "status": status
        })
    return result


def _save_keys_to_env():
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(dotenv_path):
        dotenv_path = ".env"
    lines = []
    if os.path.exists(dotenv_path):
        with open(dotenv_path, "r") as f:
            lines = f.readlines()
    keys_str = ",".join(API_KEYS)
    new_line = f"CASTAI_API_KEYS={keys_str}\n"
    found = False
    for idx, line in enumerate(lines):
        if line.startswith("CASTAI_API_KEYS="):
            lines[idx] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)
    with open(dotenv_path, "w") as f:
        f.writelines(lines)


# Auth helpers
def verify_admin_password(password: str) -> bool:
    return bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH)


# Stats helpers for pagination
async def get_paginated_logs(page: int = 1, per_page: int = 20, search: str = "", sort_by: str = "created_at", sort_order: str = "DESC"):
    """Get paginated request logs with search and sorting."""
    offset = (page - 1) * per_page
    allowed_sort = {"created_at", "model", "status_code", "latency_ms"}
    if sort_by not in allowed_sort:
        sort_by = "created_at"
    sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"

    search_clause = ""
    search_args = []
    if search:
        search_clause = "WHERE model ILIKE $1 OR key_prefix ILIKE $1"
        search_args = [f"%{search}%"]

    # Count total
    count_query = f"SELECT COUNT(*) FROM request_logs {search_clause}"
    total_row = await db_fetchrow(count_query, *search_args)
    total = total_row["count"] if total_row else 0

    # Fetch page
    args = search_args + [per_page, offset]
    arg_offset = len(search_args) + 1
    query = f"""
        SELECT model, status_code, key_prefix, rotated, latency_ms, created_at
        FROM request_logs
        {search_clause}
        ORDER BY {sort_by} {sort_order}
        LIMIT ${arg_offset} OFFSET ${arg_offset + 1}
    """
    rows = await db_fetch(query, *args)

    logs = []
    for r in rows:
        ts = r["created_at"]
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts = str(ts)
        logs.append({
            "timestamp": ts,
            "model": r["model"],
            "status_code": r["status_code"],
            "key_used": r["key_prefix"],
            "rotated": r["rotated"],
            "latency_ms": r["latency_ms"]
        })

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page
    }
