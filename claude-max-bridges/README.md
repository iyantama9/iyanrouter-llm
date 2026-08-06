# Claude Max Bridges - Multi-Instance Setup

Setup multiple Claude Max Bridge instances untuk menggunakan beberapa Claude Max accounts sekaligus di router.

## Structure

```
claude-max-bridges/
├── bridge-template/          # Template dari git clone
├── bridge-1/                 # Instance 1 (port 8787)
├── bridge-2/                 # Instance 2 (port 8788)
├── bridge-N/                 # Instance N (port 8787+N-1)
├── setup-multi-instance.sh   # Setup script
├── manage-bridges.sh         # Start/stop all bridges
└── README.md                 # This file
```

## Quick Setup

### 1. Run Setup Script

```bash
cd claude-max-bridges
bash setup-multi-instance.sh
```

Script akan tanya berapa banyak Claude Max accounts yang kamu punya, lalu create instance untuk masing-masing.

### 2. Authenticate Each Instance

Setiap instance butuh authenticate dengan Claude account yang berbeda:

```bash
# Instance 1
cd bridge-1
HOME=$(pwd) claude auth login

# Instance 2
cd bridge-2
HOME=$(pwd) claude auth login

# And so on...
```

**Tips:** 
- Use `claude auth login --no-browser` untuk headless/VPS
- Each instance stores its auth in its own directory

### 3. Start Bridges

```bash
# Start all at once
bash manage-bridges.sh start

# Or start individually
cd bridge-1 && ./start.sh &
cd bridge-2 && ./start.sh &
```

### 4. Get API Keys

Pada first run, setiap bridge akan print admin API key:

```
🔑 Default admin API key: sk-mb-admin-a1b2c3d4...
```

**Save these keys!** You'll need them untuk akses bridge.

## Instance Ports

Each instance runs on sequential ports starting from 8787:

```
bridge-1 → http://localhost:8787
bridge-2 → http://localhost:8788
bridge-3 → http://localhost:8789
...
```

## Testing Bridges

```bash
# Test bridge-1
curl http://localhost:8787/v1/models \
  -H "Authorization: Bearer sk-mb-admin-..."

# Test bridge-2
curl http://localhost:8788/v1/models \
  -H "Authorization: Bearer sk-mb-admin-..."
```

## Integration with Main Router

### Option A: Direct Integration (Recommended)

Add bridge URLs sebagai upstream providers di router kamu:

Edit `app/config.py`:

```python
# Claude Max Bridge providers
CLAUDE_BRIDGE_URLS = [
    "http://localhost:8787",
    "http://localhost:8788",
    "http://localhost:8789",
]

CLAUDE_BRIDGE_KEYS = [
    "sk-mb-admin-xxx1",
    "sk-mb-admin-xxx2",
    "sk-mb-admin-xxx3",
]
```

Lalu tambahkan routing logic untuk load balance across bridges.

### Option B: Proxy through Router

Create a new provider type `claude-max` di router yang automatically rotates across bridge instances.

## Management Commands

```bash
# Start all bridges
bash manage-bridges.sh start

# Stop all bridges
bash manage-bridges.sh stop

# Restart all bridges
bash manage-bridges.sh restart

# Check status
bash manage-bridges.sh status

# View logs
bash manage-bridges.sh logs
```

## Troubleshooting

### Bridge won't start

**Check authentication:**
```bash
cd bridge-1
HOME=$(pwd) claude auth status
```

**Check logs:**
```bash
cd bridge-1
tail -f bridge.log
```

### Port already in use

Edit `.env` in instance directory:
```bash
PORT=9000  # Change to different port
```

### Claude CLI not found

Install Claude Code CLI:
```bash
npm install -g @anthropic-ai/claude
```

Or use the installer from template:
```bash
cd bridge-template
bash install.sh
```

## Production Deployment

### Using PM2

```bash
# Install PM2
npm install -g pm2

# Start all bridges with PM2
cd claude-max-bridges
pm2 start bridge-1/start.sh --name bridge-1
pm2 start bridge-2/start.sh --name bridge-2

# Save PM2 configuration
pm2 save
pm2 startup
```

### Using Docker

Each instance can be dockerized. Create `Dockerfile` in instance directory:

```dockerfile
FROM python:3.11-slim

# Install Node.js for Claude CLI
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Copy pre-authenticated claude config
# (you need to authenticate first on host, then copy ~/.claude)

CMD ["python", "main.py"]
```

## Rate Limits

Default per-instance:
- **30 requests/minute** per user
- **100,000 tokens/minute** per user

Edit `.env` to adjust:
```bash
RATE_LIMIT_RPM=50
RATE_LIMIT_TPM=200000
```

## API Endpoints

Each bridge exposes:

### OpenAI Compatible
- `POST /v1/chat/completions` - Chat
- `GET /v1/models` - List models

### Anthropic Compatible
- `POST /v1/messages` - Messages API

### Admin
- `GET /v1/admin/keys` - List API keys
- `POST /v1/admin/keys` - Create key
- `DELETE /v1/admin/keys/{id}` - Delete key
- `GET /v1/admin/stats` - Usage stats

## Models Available

All bridges support these models:

| Model ID | Alias | Best For |
|----------|-------|----------|
| `claude-opus-4` | `opus` | Complex reasoning, coding |
| `claude-sonnet-4` | `sonnet` | Balanced tasks |
| `claude-haiku-4.5` | `haiku` | Quick responses |
| `claude-fable` | `fable` | Creative writing |

## Security Notes

1. **Don't expose bridges to public internet** - mereka bypass Claude's direct API billing
2. **Keep API keys secret** - each bridge key gives full access to that Claude account
3. **Use strong OAUTH_MASTER_PASSWORD** in `.env`
4. **Firewall rules** - only allow localhost or your router IP

## Cost Savings

With Claude Max subscription ($20/month unlimited):
- Direct API: $15 per million input tokens (Opus)
- With bridges: **$0** (uses Max subscription)

If you use 100M tokens/month with 3 accounts:
- Direct API cost: ~$1,500/month
- With Max bridges: **$60/month** (3 × $20)

**Savings: $1,440/month** 💰

## Support

- Bridge repo: https://github.com/mocasus/claude-max-bridge
- Issues: Open issue di repo
- Router integration: Ask in this project

---

**Setup Status:** Run `bash setup-multi-instance.sh` to begin
