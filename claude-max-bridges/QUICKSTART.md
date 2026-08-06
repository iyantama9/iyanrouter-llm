# Claude Max Bridge - Quick Start Guide

## Setup in 5 Minutes

### Step 1: Run Setup (1 minute)

```bash
cd d:/Project/Router-Iyan/llm-router/claude-max-bridges
bash setup-multi-instance.sh
```

Ketik berapa banyak Claude Max accounts yang kamu punya (contoh: 3).

Script akan create `bridge-1`, `bridge-2`, `bridge-3` folders dengan Python venv dan dependencies.

### Step 2: Authenticate Each Bridge (2 minutes)

**Important:** Setiap bridge butuh Claude account yang berbeda.

```bash
# Bridge 1 - Use first Claude Max account
cd bridge-1
HOME=$(pwd) claude auth login

# Bridge 2 - Use second Claude Max account  
cd ../bridge-2
HOME=$(pwd) claude auth login

# Bridge 3 - Use third Claude Max account
cd ../bridge-3
HOME=$(pwd) claude auth login
```

**Tips:**
- Browser akan terbuka untuk login
- Login dengan account Claude Max yang berbeda di setiap bridge
- For headless server: gunakan `--no-browser` flag

### Step 3: Start All Bridges (30 seconds)

```bash
cd d:/Project/Router-Iyan/llm-router/claude-max-bridges
bash manage-bridges.sh start
```

Output:
```
Starting all bridge instances...
  ▶️  Starting bridge-1 on port 8787...
  ✅ bridge-1 started
  ▶️  Starting bridge-2 on port 8788...
  ✅ bridge-2 started
  ▶️  Starting bridge-3 on port 8789...
  ✅ bridge-3 started
```

### Step 4: Get API Keys (1 minute)

Check logs untuk setiap bridge untuk grab admin API key:

```bash
# Bridge 1
cat bridge-1/bridge.log | grep "admin API key"

# Bridge 2  
cat bridge-2/bridge.log | grep "admin API key"

# Bridge 3
cat bridge-3/bridge.log | grep "admin API key"
```

Output akan seperti:
```
🔑 Default admin API key: sk-mb-admin-a1b2c3d4...
```

**Save these keys!** You'll need them.

### Step 5: Test Bridges (30 seconds)

```bash
# Test bridge-1
curl http://localhost:8787/v1/models \
  -H "Authorization: Bearer sk-mb-admin-YOUR_KEY_1"

# Test bridge-2
curl http://localhost:8788/v1/models \
  -H "Authorization: Bearer sk-mb-admin-YOUR_KEY_2"

# Test bridge-3
curl http://localhost:8789/v1/models \
  -H "Authorization: Bearer sk-mb-admin-YOUR_KEY_3"
```

Expected response:
```json
{
  "object": "list",
  "data": [
    {"id": "opus", "object": "model"},
    {"id": "sonnet", "object": "model"},
    {"id": "haiku", "object": "model"},
    {"id": "fable", "object": "model"}
  ]
}
```

---

## ✅ You're Done!

Bridges are running and ready to use.

## Next: Integrate with Main Router

Add bridge URLs to your main router's `.env`:

```bash
# Claude Max Bridges
CLAUDE_BRIDGE_URLS=http://localhost:8787,http://localhost:8788,http://localhost:8789
CLAUDE_BRIDGE_KEYS=sk-mb-admin-KEY1,sk-mb-admin-KEY2,sk-mb-admin-KEY3
CLAUDE_BRIDGE_MODELS=opus,sonnet,haiku,fable
```

Or manually add to `app/config.py`:

```python
# Claude Max Bridge provider
CLAUDE_BRIDGE_URLS = os.getenv("CLAUDE_BRIDGE_URLS", "").split(",")
CLAUDE_BRIDGE_KEYS = os.getenv("CLAUDE_BRIDGE_KEYS", "").split(",")
CLAUDE_BRIDGE_MODELS = ["opus", "sonnet", "haiku", "fable"]
```

---

## Common Commands

```bash
# Check status
bash manage-bridges.sh status

# Restart all
bash manage-bridges.sh restart

# Stop all
bash manage-bridges.sh stop

# View logs for bridge-1
bash manage-bridges.sh logs 1

# View logs for bridge-2
bash manage-bridges.sh logs 2
```

---

## Troubleshooting

### "claude: command not found"

Install Claude CLI:
```bash
npm install -g @anthropic-ai/claude
```

### "Port already in use"

Stop other services on ports 8787-8789, or edit `.env` in each bridge folder to use different ports.

### Bridge won't start

Check authentication:
```bash
cd bridge-1
HOME=$(pwd) claude auth status
```

If not authenticated, run:
```bash
HOME=$(pwd) claude auth login
```

### Can't access bridge from browser

Bridges run on localhost only by default. To expose externally, edit `main.py` in each bridge:

```python
# Change from:
uvicorn.run(app, host="127.0.0.1", port=PORT)

# To:
uvicorn.run(app, host="0.0.0.0", port=PORT)
```

**Warning:** Only do this if you know what you're doing. Add firewall rules!

---

## Cost Savings Calculator

| Scenario | Direct API Cost | With Max Bridges | Savings |
|----------|----------------|------------------|---------|
| 50M tokens/month | ~$750/month | $20/month (1 account) | **$730/month** |
| 100M tokens/month | ~$1,500/month | $40/month (2 accounts) | **$1,460/month** |
| 200M tokens/month | ~$3,000/month | $60/month (3 accounts) | **$2,940/month** |

*(Based on Claude Opus pricing: $15 per 1M input tokens)*

---

**Setup Complete!** 🎉

Your Claude Max subscriptions are now accessible via OpenAI-compatible API.
