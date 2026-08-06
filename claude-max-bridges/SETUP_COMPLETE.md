# ✅ Claude Max Bridge Setup - COMPLETE

**Date:** 2026-07-26  
**Location:** `d:/Project/Router-Iyan/llm-router/claude-max-bridges/`

---

## 📦 What's Been Set Up

### File Structure Created

```
claude-max-bridges/
├── bridge-template/              # ✅ Cloned from GitHub
│   └── (original bridge code)
├── setup-multi-instance.sh       # ✅ Setup script for multiple instances
├── manage-bridges.sh             # ✅ Start/stop/status management
├── README.md                     # ✅ Full documentation
├── QUICKSTART.md                 # ✅ 5-minute setup guide
└── SETUP_COMPLETE.md            # ✅ This file
```

### Scripts Available

| Script | Purpose | Status |
|--------|---------|--------|
| `setup-multi-instance.sh` | Create N bridge instances | ✅ Executable |
| `manage-bridges.sh` | Start/stop/restart bridges | ✅ Executable |

### Documentation

| File | Description |
|------|-------------|
| `README.md` | Complete guide with integration, troubleshooting, deployment |
| `QUICKSTART.md` | 5-minute setup walkthrough |
| `SETUP_COMPLETE.md` | This summary |

---

## 🎯 What This Does

Claude Max Bridge converts your **Claude Max subscription** into an **OpenAI-compatible API**, allowing you to:

1. **Use Max subscription instead of paying for API** ($0 vs $15/1M tokens)
2. **Connect any OpenAI-compatible tool** (Cursor, Cline, Continue.dev, etc.)
3. **Load balance across multiple Max accounts**
4. **Integrate with your existing router** for unified access

**Architecture:**

```
Your Tools/Apps
    ↓
Main LLM Router (your existing router)
    ↓
┌─────────────────────┬─────────────────────┬─────────────────────┐
│                     │                     │                     │
Bridge-1 (8787)    Bridge-2 (8788)    Bridge-3 (8789)
↓                   ↓                   ↓
Claude Max Acc 1    Claude Max Acc 2    Claude Max Acc 3
```

---

## 📋 Next Steps (To Use This)

### Step 1: Run Setup

```bash
cd d:/Project/Router-Iyan/llm-router/claude-max-bridges
bash setup-multi-instance.sh
```

Input: Number of Claude Max accounts you have (e.g., 3)

### Step 2: Authenticate Each Instance

```bash
# Instance 1
cd bridge-1
HOME=$(pwd) claude auth login

# Instance 2
cd bridge-2
HOME=$(pwd) claude auth login

# Repeat for each instance...
```

**Important:** Use different Claude Max account for each instance!

### Step 3: Start Bridges

```bash
cd ..
bash manage-bridges.sh start
```

### Step 4: Get API Keys

```bash
cat bridge-1/bridge.log | grep "admin API key"
cat bridge-2/bridge.log | grep "admin API key"
cat bridge-3/bridge.log | grep "admin API key"
```

Save these keys!

### Step 5: Test

```bash
curl http://localhost:8787/v1/models \
  -H "Authorization: Bearer YOUR_KEY"
```

---

## 🔗 Integration with Main Router

### Option A: Add as New Provider Type

Edit `app/config.py`:

```python
# Claude Max Bridge Configuration
CLAUDE_BRIDGE_ENABLED = os.getenv("CLAUDE_BRIDGE_ENABLED", "false").lower() == "true"
CLAUDE_BRIDGE_URLS_RAW = os.getenv("CLAUDE_BRIDGE_URLS", "")
CLAUDE_BRIDGE_KEYS_RAW = os.getenv("CLAUDE_BRIDGE_KEYS", "")

CLAUDE_BRIDGE_URLS = [u.strip() for u in CLAUDE_BRIDGE_URLS_RAW.split(",") if u.strip()]
CLAUDE_BRIDGE_KEYS = [k.strip() for k in CLAUDE_BRIDGE_KEYS_RAW.split(",") if k.strip()]
CLAUDE_BRIDGE_MODELS = ["opus", "sonnet", "haiku", "fable"]
```

Add to `.env`:

```env
CLAUDE_BRIDGE_ENABLED=true
CLAUDE_BRIDGE_URLS=http://localhost:8787,http://localhost:8788,http://localhost:8789
CLAUDE_BRIDGE_KEYS=sk-mb-admin-xxx1,sk-mb-admin-xxx2,sk-mb-admin-xxx3
```

### Option B: Standalone Usage

Use bridges directly without router:

```bash
curl http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-mb-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "opus",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 💰 Cost Savings

**Before (Direct API):**
- Opus: $15 per 1M input tokens
- 200M tokens/month = **$3,000/month**

**After (With 3 Max Bridges):**
- 3 × Claude Max subscription = **$60/month**
- Savings: **$2,940/month** 💸

---

## 🛠️ Management Commands

```bash
# Check status of all bridges
bash manage-bridges.sh status

# Start all bridges
bash manage-bridges.sh start

# Stop all bridges
bash manage-bridges.sh stop

# Restart all bridges
bash manage-bridges.sh restart

# View logs for bridge-1
bash manage-bridges.sh logs 1
```

---

## 🔒 Security Notes

1. **Bridges contain auth tokens** - added to `.gitignore`
2. **Bridges run on localhost** - not exposed to internet by default
3. **API keys give full access** - keep them secret
4. **Each bridge = 1 Max account** - don't share accounts across bridges

---

## 📊 What's Tracked by Git

| Item | Tracked? | Reason |
|------|----------|--------|
| `setup-multi-instance.sh` | ✅ Yes | Setup script |
| `manage-bridges.sh` | ✅ Yes | Management script |
| `README.md` / `QUICKSTART.md` | ✅ Yes | Documentation |
| `bridge-template/` | ✅ Yes | Clean template from GitHub |
| `bridge-1/`, `bridge-2/`, etc. | ❌ No | Contains auth tokens |

---

## 📚 Documentation Files

- **QUICKSTART.md** - Start here! 5-minute setup guide
- **README.md** - Complete reference (deployment, troubleshooting, integration)
- **SETUP_COMPLETE.md** - This summary

---

## ✅ Setup Status

- [x] Bridge template cloned from GitHub
- [x] Setup scripts created and executable
- [x] Management scripts created
- [x] Documentation written
- [x] .gitignore updated (auth tokens protected)
- [ ] **TODO:** Run `setup-multi-instance.sh`
- [ ] **TODO:** Authenticate each bridge
- [ ] **TODO:** Start bridges
- [ ] **TODO:** Integrate with main router (optional)

---

## 🚀 Ready to Use!

Everything is prepared. Follow **QUICKSTART.md** to get your bridges running in 5 minutes.

**Questions?**
- Check `README.md` for detailed docs
- Check `QUICKSTART.md` for quick setup
- Bridge repo: https://github.com/mocasus/claude-max-bridge
