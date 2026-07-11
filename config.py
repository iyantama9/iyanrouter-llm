import os
import time
import asyncio
import hashlib
import bcrypt
from dotenv import load_dotenv
from database import execute as db_execute, fetch as db_fetch, fetchrow as db_fetchrow

load_dotenv()

DEFAULT_UPSTREAM_URL_RAW = os.getenv("DEFAULT_UPSTREAM_URL")
if not DEFAULT_UPSTREAM_URL_RAW:
    raise ValueError("DEFAULT_UPSTREAM_URL environment variable is not set")

# Models configuration
KIMCHI_MODELS_RAW = os.getenv("KIMCHI_MODELS", "")
CAVOTI_MODELS_RAW = os.getenv("CAVOTI_MODELS", "")
BLUESMINDS_MODELS_RAW = os.getenv("BLUESMINDS_MODELS", "")

KIMCHI_MODELS = [m.strip() for m in KIMCHI_MODELS_RAW.split(",") if m.strip()]
CAVOTI_MODELS = [m.strip() for m in CAVOTI_MODELS_RAW.split(",") if m.strip()]
BLUESMINDS_MODELS = [m.strip() for m in BLUESMINDS_MODELS_RAW.split(",") if m.strip()]

ROUTER_DOMAIN = os.getenv("ROUTER_DOMAIN", "localhost")

CAVOTI_API_KEY = os.getenv("CAVOTI_API_KEY")
BLUESMINDS_API_KEY = os.getenv("BLUESMINDS_API_KEY")

ROUTER_PASSWORD = os.getenv("ROUTER_PASSWORD")
PORT_STR = os.getenv("PORT")
SSL_KEYFILE = os.getenv("SSL_KEYFILE")
SSL_CERTFILE = os.getenv("SSL_CERTFILE")

DEFAULT_UPSTREAM_URL = DEFAULT_UPSTREAM_URL_RAW.rstrip("/")
CAVOTI_BASE_URL = os.getenv("CAVOTI_BASE_URL", "https://sg.cavoti.com/v1").rstrip("/")
BLUESMINDS_BASE_URL = os.getenv("BLUESMINDS_BASE_URL", "https://api.bluesminds.com/v1").rstrip("/")
SHOW_REASONING = os.getenv("SHOW_REASONING", "true").lower() == "true"
AUGMENT_SYSTEM_PROMPT = os.getenv("AUGMENT_SYSTEM_PROMPT", "true").lower() == "true"
# Rotate key proactively if time-to-first-token exceeds this (ms). 0 = disabled.
SLOW_RESPONSE_THRESHOLD_MS = int(os.getenv("SLOW_RESPONSE_THRESHOLD_MS", "10000"))
# Minutes before a "Limited" key is automatically reset to "Standby". 0 = disabled.
LIMIT_COOLDOWN_MINUTES = int(os.getenv("LIMIT_COOLDOWN_MINUTES", "60"))

if not PORT_STR:
    raise ValueError("PORT environment variable is not set")

PORT = int(PORT_STR)

# Admin credentials - hash if plaintext, or load hash from env
_ADMIN_HASH = os.getenv("ADMIN_PASSWORD_HASH")
if _ADMIN_HASH:
    # Hash already exists in env
    ADMIN_PASSWORD_HASH = _ADMIN_HASH.encode()
else:
    _raw_password = os.getenv("ADMIN_PASSWORD")
    if not _raw_password:
        raise ValueError("ADMIN_PASSWORD environment variable must be set on first run to generate hash.")
    ADMIN_PASSWORD_HASH = bcrypt.hashpw(_raw_password.encode(), bcrypt.gensalt())
    _bcrypt_hash_str = ADMIN_PASSWORD_HASH.decode()
    print(f"[SETUP] Generated bcrypt hash. Save this to ADMIN_PASSWORD_HASH env var:\n{_bcrypt_hash_str}")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "iyanadmin")

# Session secret: stable across restarts (derived from admin creds)
SESSION_SECRET = hashlib.sha256(f"{ADMIN_USERNAME}:{os.getenv('ADMIN_PASSWORD', '')}:kimchi-secret-v2".encode()).hexdigest()

# In-memory state (primary for fast access, DB is persistence)
API_KEYS = []
CV_API_KEYS = []
BM_API_KEYS = []
key_statuses = {}
key_limited_at: dict[str, float] = {}  # key_value -> time.time() when marked Limited
total_requests = 0
total_tokens = 0
failover_count = 0
recent_requests = []
START_TIME = time.time()
current_key_index = 0
current_cv_key_index = 0
current_bm_key_index = 0


def _bg(coro):
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        pass


async def init_state_from_db():
    global API_KEYS, CV_API_KEYS, BM_API_KEYS, key_statuses, total_requests, failover_count, current_key_index, current_cv_key_index, current_bm_key_index, START_TIME

    API_KEYS.clear()
    CV_API_KEYS.clear()
    BM_API_KEYS.clear()
    key_statuses.clear()

    # Load keys from DB
    rows = await db_fetch("SELECT key_value, key_prefix, status, provider FROM api_keys ORDER BY id")
    if rows:
        for r in rows:
            val = r["key_value"]
            provider = r.get("provider", "kc")
            if provider == "cv":
                CV_API_KEYS.append(val)
            elif provider == "bm":
                BM_API_KEYS.append(val)
            else:
                API_KEYS.append(val)
            key_statuses[val] = r["status"]
    else:
        # Seed from .env fallback for Kimchi keys
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
                "INSERT INTO api_keys (key_value, key_prefix, status, provider) VALUES ($1, $2, $3, 'kc') ON CONFLICT DO NOTHING",
                k, prefix, key_statuses[k]
            )

    # Always ensure CV key from env is seeded and present
    cv_key = CAVOTI_API_KEY
    if cv_key and cv_key not in CV_API_KEYS:
        CV_API_KEYS.append(cv_key)
        key_statuses[cv_key] = "Active"
        prefix = cv_key[:15] + "..." if len(cv_key) > 15 else cv_key
        await db_execute(
            "INSERT INTO api_keys (key_value, key_prefix, status, provider) VALUES ($1, $2, $3, 'cv') ON CONFLICT DO NOTHING",
            cv_key, prefix, key_statuses[cv_key]
        )

    # Always ensure BM key from env is seeded and present
    bm_key = BLUESMINDS_API_KEY
    if bm_key and bm_key not in BM_API_KEYS:
        BM_API_KEYS.append(bm_key)
        key_statuses[bm_key] = "Active"
        prefix = bm_key[:15] + "..." if len(bm_key) > 15 else bm_key
        await db_execute(
            "INSERT INTO api_keys (key_value, key_prefix, status, provider) VALUES ($1, $2, $3, 'bm') ON CONFLICT DO NOTHING",
            bm_key, prefix, key_statuses[bm_key]
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

    for i, k in enumerate(CV_API_KEYS):
        if key_statuses.get(k) == "Active":
            current_cv_key_index = i
            break
    else:
        if CV_API_KEYS:
            current_cv_key_index = 0
            key_statuses[CV_API_KEYS[0]] = "Active"
            await db_execute(
                "UPDATE api_keys SET status = 'Active' WHERE key_value = $1",
                CV_API_KEYS[0]
            )

    for i, k in enumerate(BM_API_KEYS):
        if key_statuses.get(k) == "Active":
            current_bm_key_index = i
            break
    else:
        if BM_API_KEYS:
            current_bm_key_index = 0
            key_statuses[BM_API_KEYS[0]] = "Active"
            await db_execute(
                "UPDATE api_keys SET status = 'Active' WHERE key_value = $1",
                BM_API_KEYS[0]
            )

    # Load stats from DB
    tr = await db_fetchrow("SELECT value FROM server_config WHERE key = 'total_requests'")
    if tr:
        total_requests = int(tr["value"])
    tt = await db_fetchrow("SELECT value FROM server_config WHERE key = 'total_tokens'")
    if tt:
        total_tokens = int(tt["value"])
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
            import datetime
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            ts = ts.astimezone(datetime.timezone(datetime.timedelta(hours=7)))
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

    # Clean slate on startup — reset any previously Limited/Slow keys to Standby
    for key, status in list(key_statuses.items()):
        if status in ("Limited", "Slow"):
            key_statuses[key] = "Standby"
            await db_execute("UPDATE api_keys SET status = 'Standby' WHERE key_value = $1", key)

    print(f"[INIT] Loaded {len(API_KEYS)} keys, {total_requests} total requests, {failover_count} failovers from DB")


async def auto_reset_limited_keys():
    """Reset keys that have been Limited for longer than LIMIT_COOLDOWN_MINUTES."""
    if LIMIT_COOLDOWN_MINUTES <= 0:
        return []
    now = time.time()
    cooldown_secs = LIMIT_COOLDOWN_MINUTES * 60
    reset_keys = []
    for key, limited_at in list(key_limited_at.items()):
        if now - limited_at >= cooldown_secs and key_statuses.get(key) == "Limited":
            key_statuses[key] = "Standby"
            await db_execute("UPDATE api_keys SET status = 'Standby' WHERE key_value = $1", key)
            del key_limited_at[key]
            reset_keys.append(key[:15] + "...")
    if reset_keys:
        print(f"[AUTO-RESET] Auto-reset {len(reset_keys)} Limited key(s) to Standby: {reset_keys}")
    return reset_keys


def get_current_key():
    if not API_KEYS:
        return ""
    return API_KEYS[current_key_index]


def rotate_key(reason: str = "Limited"):
    global current_key_index, failover_count
    if len(API_KEYS) <= 1:
        return get_current_key()
    old_key = get_current_key()
    key_statuses[old_key] = reason
    if reason == "Limited":
        key_limited_at[old_key] = time.time()
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    new_key = get_current_key()
    key_statuses[new_key] = "Active"
    failover_count += 1
    print(f"[LOG] Rotated kc key → index {current_key_index}: {new_key[:15]}... (reason: {reason})")
    _bg(db_execute("UPDATE api_keys SET status = $1 WHERE key_value = $2", reason, old_key))
    _bg(db_execute("UPDATE api_keys SET status = 'Active' WHERE key_value = $1", new_key))
    _bg(db_execute("INSERT INTO server_config (key, value) VALUES ('failover_count', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", str(failover_count)))
    return new_key


def get_current_cv_key():
    if not CV_API_KEYS:
        return CAVOTI_API_KEY
    return CV_API_KEYS[current_cv_key_index]


def rotate_cv_key(reason: str = "Limited"):
    global current_cv_key_index, failover_count
    if len(CV_API_KEYS) <= 1:
        return get_current_cv_key()
    old_key = get_current_cv_key()
    key_statuses[old_key] = reason
    if reason == "Limited":
        key_limited_at[old_key] = time.time()
    current_cv_key_index = (current_cv_key_index + 1) % len(CV_API_KEYS)
    new_key = get_current_cv_key()
    key_statuses[new_key] = "Active"
    failover_count += 1
    print(f"[LOG] Rotated cv key → index {current_cv_key_index}: {new_key[:15]}... (reason: {reason})")
    _bg(db_execute("UPDATE api_keys SET status = $1 WHERE key_value = $2", reason, old_key))
    _bg(db_execute("UPDATE api_keys SET status = 'Active' WHERE key_value = $1", new_key))
    _bg(db_execute("INSERT INTO server_config (key, value) VALUES ('failover_count', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", str(failover_count)))
    return new_key


def get_current_bm_key():
    if not BM_API_KEYS:
        return BLUESMINDS_API_KEY
    return BM_API_KEYS[current_bm_key_index]


def rotate_bm_key(reason: str = "Limited"):
    global current_bm_key_index, failover_count
    if len(BM_API_KEYS) <= 1:
        return get_current_bm_key()
    old_key = get_current_bm_key()
    key_statuses[old_key] = reason
    if reason == "Limited":
        key_limited_at[old_key] = time.time()
    current_bm_key_index = (current_bm_key_index + 1) % len(BM_API_KEYS)
    new_key = get_current_bm_key()
    key_statuses[new_key] = "Active"
    failover_count += 1
    print(f"[LOG] Rotated bm key → index {current_bm_key_index}: {new_key[:15]}... (reason: {reason})")
    _bg(db_execute("UPDATE api_keys SET status = $1 WHERE key_value = $2", reason, old_key))
    _bg(db_execute("UPDATE api_keys SET status = 'Active' WHERE key_value = $1", new_key))
    _bg(db_execute("INSERT INTO server_config (key, value) VALUES ('failover_count', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", str(failover_count)))
    return new_key


def add_request_log(model, status_code, key_used, rotated, latency_ms, input_tokens: int = 0, output_tokens: int = 0):
    global total_requests, total_tokens
    total_requests += 1
    total_tokens += (input_tokens + output_tokens)
    import datetime
    timestamp = (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%H:%M:%S")
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
        "INSERT INTO request_logs (model, status_code, key_prefix, rotated, latency_ms, input_tokens, output_tokens) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        model, status_code, log_item["key_used"], rotated, latency_ms, input_tokens, output_tokens
    ))
    _bg(db_execute(
        "INSERT INTO server_config (key, value) VALUES ('total_requests', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        str(total_requests)
    ))
    _bg(db_execute(
        "INSERT INTO server_config (key, value) VALUES ('total_tokens', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        str(total_tokens)
    ))


def add_api_key(new_key: str, key_type: str = "auto"):
    global API_KEYS, CV_API_KEYS, BM_API_KEYS
    new_key = new_key.strip()
    if not new_key:
        return False, "Key cannot be empty"
    if new_key in API_KEYS or new_key in CV_API_KEYS or new_key in BM_API_KEYS:
        return False, "Key already exists"
        
    if key_type == "cv":
        CV_API_KEYS.append(new_key)
        provider = "cv"
    elif key_type == "bm":
        BM_API_KEYS.append(new_key)
        provider = "bm"

    else:
        API_KEYS.append(new_key)
        provider = "kc"
        
    key_statuses[new_key] = "Standby"
    prefix = new_key[:15] + "..." if len(new_key) > 15 else new_key
    # Save to DB
    _bg(db_execute(
        "INSERT INTO api_keys (key_value, key_prefix, status, provider) VALUES ($1, $2, 'Standby', $3)",
        new_key, prefix, provider
    ))
    # Also keep .env synced as backup for castai keys
    if provider == "kc":
        _save_keys_to_env()
    return True, "Key added successfully"


def remove_api_key(key_prefix: str):
    global API_KEYS, CV_API_KEYS, current_key_index, current_cv_key_index
    target_key = None
    target_list = None
    
    for key in API_KEYS:
        if key.startswith(key_prefix):
            target_key = key
            target_list = API_KEYS
            break
            

    if not target_key:
        for key in CV_API_KEYS:
            if key.startswith(key_prefix):
                target_key = key
                target_list = CV_API_KEYS
                break

    if not target_key:
        for key in BM_API_KEYS:
            if key.startswith(key_prefix):
                target_key = key
                target_list = BM_API_KEYS
                break

    if not target_key:
        return False, "Key not found"
    
    if len(target_list) <= 1:
        return False, "Cannot delete the last remaining key of this type"
        
    if target_list == API_KEYS:
        active_key = get_current_key()
        if target_key == active_key:
            rotate_key()
        API_KEYS.remove(target_key)
        try:
            current_key_index = API_KEYS.index(get_current_key()) if get_current_key() in API_KEYS else 0
        except Exception:
            current_key_index = 0

    elif target_list == CV_API_KEYS:
        active_key = get_current_cv_key()
        if target_key == active_key:
            rotate_cv_key()
        CV_API_KEYS.remove(target_key)
        try:
            current_cv_key_index = CV_API_KEYS.index(get_current_cv_key()) if get_current_cv_key() in CV_API_KEYS else 0
        except Exception:
            current_cv_key_index = 0
    else:
        active_key = get_current_bm_key()
        if target_key == active_key:
            rotate_bm_key()
        BM_API_KEYS.remove(target_key)
        try:
            current_bm_key_index = BM_API_KEYS.index(get_current_bm_key()) if get_current_bm_key() in BM_API_KEYS else 0
        except Exception:
            current_bm_key_index = 0

    if target_key in key_statuses:
        del key_statuses[target_key]
        
    # Remove from DB
    _bg(db_execute("DELETE FROM api_keys WHERE key_value = $1", target_key))
    if target_list == API_KEYS:
        _save_keys_to_env()
    return True, "Key removed successfully"


def reset_key_status(key_prefix: str):
    for key in API_KEYS + CV_API_KEYS + BM_API_KEYS:
        if key.startswith(key_prefix):
            key_statuses[key] = "Standby"
            _bg(db_execute("UPDATE api_keys SET status = 'Standby' WHERE key_value = $1", key))
            return True, "Key status reset to Standby"
    return False, "Key not found"


def set_active_key(key_prefix: str, provider: str = None):
    global current_key_index, current_cv_key_index, current_bm_key_index
    target_key = None
    target_list = None

    if provider == "kc": target_list = API_KEYS

    elif provider == "cv": target_list = CV_API_KEYS
    elif provider == "bm": target_list = BM_API_KEYS
    else:
        # Auto detect list if provider not explicitly passed
        for lst in [API_KEYS, CV_API_KEYS, BM_API_KEYS]:
            for k in lst:
                if k.startswith(key_prefix):
                    target_key = k
                    target_list = lst
                    break
            if target_key: break

    if not target_list: return False, "Target list not found"
    
    if not target_key:
        for k in target_list:
            if k.startswith(key_prefix):
                target_key = k
                break
                
    if not target_key:
        return False, "Key not found"

    # Set all in target_list to Standby
    for k in target_list:
        if key_statuses.get(k) == "Active":
            key_statuses[k] = "Standby"
            _bg(db_execute("UPDATE api_keys SET status = 'Standby' WHERE key_value = $1", k))
            
    # Set target to Active
    key_statuses[target_key] = "Active"
    _bg(db_execute("UPDATE api_keys SET status = 'Active' WHERE key_value = $1", target_key))
    
    # Update index
    idx = target_list.index(target_key)
    if target_list == API_KEYS: current_key_index = idx

    elif target_list == CV_API_KEYS: current_cv_key_index = idx
    elif target_list == BM_API_KEYS: current_bm_key_index = idx
    
    return True, "Key set as Active"


def get_masked_keys():
    result = []
    for idx, key in enumerate(API_KEYS + CV_API_KEYS + BM_API_KEYS):
        status = key_statuses.get(key, "Standby")
        masked = key[:15] + "..." if len(key) > 15 else key
        result.append({
            "index": idx,
            "masked": masked,
            "prefix": key[:15],
            "status": status,
            "is_kc": key in API_KEYS,
            "is_cv": key in CV_API_KEYS,
            "is_bm": key in BM_API_KEYS
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
    cv_keys_str = ",".join(CV_API_KEYS)
    new_line = f"CASTAI_API_KEYS={keys_str}\n"
    new_cv_line = f"CAVOTI_API_KEYS={cv_keys_str}\n"

    found = False
    found_cv = False
    for idx, line in enumerate(lines):
        if line.startswith("CASTAI_API_KEYS="):
            lines[idx] = new_line
            found = True
        elif line.startswith("CAVOTI_API_KEYS=") or line.startswith("CAVOTI_API_KEY="):
            lines[idx] = new_cv_line
            found_cv = True

    if not found:
        lines.append(new_line)
    if not found_cv:
        lines.append(new_cv_line)
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
            import datetime
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            ts = ts.astimezone(datetime.timezone(datetime.timedelta(hours=7)))
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
