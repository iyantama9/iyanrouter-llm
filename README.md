# LLM Router - Docker Edition

Multi-provider LLM routing server with automatic failover, load balancing, and unified OpenAI-compatible API.

## 🎉 What's New - v2.0

**Docker Migration Complete!**
- ✅ Migrated from Neon PostgreSQL to local Docker PostgreSQL
- ✅ Full Docker containerization (app + database)
- ✅ 4,433 rows of data migrated successfully (100 API keys, 4,314 requests)
- ✅ SSL/HTTPS support with Let's Encrypt
- ✅ One-command deployment ready
- ✅ Production ready with auto-restart

## 🚀 Features

- **Multi-Provider Support** — Route to 9+ LLM providers (Kimchi, Cavoti, BluesMinds, byNara, Dahl, Qwen Cloud, MarketKu, Atomesus, Weize)
- **OpenAI-Compatible API** — Works with any OpenAI-compatible client
- **Automatic Failover** — Smart key rotation on rate limits and errors
- **Load Balancing** — Round-robin key rotation across 100+ API keys
- **Web Dashboard** — Real-time monitoring and key management
- **Request Logging** — Track all API calls with detailed statistics
- **Docker Ready** — Full containerization with PostgreSQL
- **SSL/HTTPS** — Built-in Let's Encrypt support
- **Chat Playground** — Test models directly in browser

## 📦 Tech Stack

- **Backend**: Python 3.11 + FastAPI + Uvicorn
- **Database**: PostgreSQL 16 (Docker)
- **Auth**: bcrypt password hashing
- **HTTP Client**: httpx for upstream requests
- **Templates**: Jinja2 + Tailwind CSS
- **Deployment**: Docker + Docker Compose

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose installed
- Domain pointing to your server (for SSL, optional)
- Ports available: 80, 443, 4000, 5432

### Local Development

```bash
# 1. Clone repository
git clone <your-repo-url>
cd llm-router

# 2. Configure environment (optional, has defaults)
cp .env.example .env
# Edit .env with your API keys

# 3. Start services
docker-compose up -d

# 4. Check status
docker-compose ps

# 5. Access dashboard
open http://localhost:4000/dashboard
```

### Production Deployment

**Option 1: Automated (from local machine)**

```bash
# Deploy everything to server
bash deploy-to-server.sh

# Setup SSL with Let's Encrypt
ssh root@178.128.59.20 'cd /root/llm-router && bash setup-ssl.sh'
```

**Option 2: Manual (on server)**

```bash
# 1. SSH to server
ssh root@178.128.59.20

# 2. Navigate to project
cd /root/llm-router

# 3. Start services
docker-compose up -d

# 4. Setup SSL (optional but recommended)
bash setup-ssl.sh

# 5. Verify deployment
docker ps
docker logs llm-router-app --tail 50
```

## 🔧 Configuration

### Environment Variables (.env)

```env
# Database (Docker PostgreSQL)
DATABASE_URL=postgresql://llm_router_user:llm_router_pass_2024@postgres:5432/llm_router

# Server Configuration
PORT=443  # Use 443 for HTTPS, 4000 for HTTP
ROUTER_DOMAIN=routers.iyantama.tech

# Security
ADMIN_USERNAME=iyanadmin
ADMIN_PASSWORD=your-secure-password
ROUTER_PASSWORD=your-router-password

# SSL/HTTPS (optional)
SSL_KEYFILE=/app/ssl/key.pem
SSL_CERTFILE=/app/ssl/cert.pem

# Provider API Keys
CASTAI_API_KEYS=key1,key2,key3
CAVOTI_API_KEY=your-cavoti-key
BLUESMINDS_API_KEY=your-bluesminds-key
NARA_API_KEYS=key1,key2
DAHL_API_KEYS=key1,key2
QWEN_CLOUD_API_KEYS=key1,key2
MARKETKU_API_KEYS=key1
ATOMESUS_API_KEYS=key1
WEIZE_API_KEYS=key1

# Provider Model Lists (comma-separated)
KIMCHI_MODELS=deepseek-v4-flash,glm-5.2-fp8,kimi-k2.7
CAVOTI_MODELS=gpt-5.5,gpt-5.6-sol,claude-sonnet-4.6
# ... etc
```

### Docker Services

**PostgreSQL Database:**
- Container: `llm-router-db`
- Port: `5432:5432`
- Volume: `postgres_data` (persistent storage)
- Health checks: Automated

**Application:**
- Container: `llm-router-app`
- Ports: `80:80`, `443:443`, `4000:4000`
- Auto-restart: `unless-stopped`
- Health checks: Automated

## 🎮 Usage

### API Endpoints

**Chat Completion (OpenAI-compatible)**

```bash
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ROUTER_PASSWORD" \
  -d '{
    "model": "deepseek-v4-flash",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ],
    "stream": true
  }'
```

**With Claude Code**

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_API_KEY=your-router-password

claude --model deepseek-v4-flash "Write a hello world"
```

**Admin Dashboard**

- Dashboard: `http://localhost:4000/dashboard`
- Login: username `iyanadmin`, password from `.env`
- Features: Key management, request logs, statistics, SSE live updates

**Chat Playground**

- Access: `http://localhost:4000/playground`
- Test models interactively
- Manage chat sessions

## 📊 Supported Providers

| Provider | Models Available | Keys Loaded |
|----------|------------------|-------------|
| Kimchi | 13 models | 13 keys |
| Cavoti | 20 models | 4 keys |
| BluesMinds | 50+ models | 2 keys |
| byNara | 35+ models | 2 keys |
| Dahl | 3 models | 8 keys |
| Qwen Cloud | 149 models | 60 keys |
| MarketKu | 11 models | 1 key |
| Atomesus | Multiple | 9 keys |
| Weize | 41 models | 1 key |

**Total: 100 API keys managing 300+ models**

## 🛠️ Management Commands

### Docker Operations

```bash
# Start all services
docker-compose up -d

# Stop services
docker-compose stop

# Restart app only
docker-compose restart app

# View logs
docker-compose logs -f

# Check status
docker-compose ps
docker ps | grep llm-router

# Stop and remove everything
docker-compose down

# Stop and remove including volumes
docker-compose down -v
```

### Database Management

```bash
# Connect to database
docker exec -it llm-router-db psql -U llm_router_user -d llm_router

# Run SQL query
docker exec llm-router-db psql -U llm_router_user -d llm_router -c "SELECT COUNT(*) FROM api_keys;"

# Backup database
docker exec llm-router-db pg_dump -U llm_router_user llm_router > backup.sql

# Restore database
docker exec -i llm-router-db psql -U llm_router_user -d llm_router < backup.sql

# View table stats
docker exec llm-router-db psql -U llm_router_user -d llm_router -c "\dt+"
```

### Monitoring

```bash
# Container stats
docker stats llm-router-app llm-router-db

# Disk usage
docker system df

# App logs (last 100 lines)
docker logs llm-router-app --tail 100

# Follow logs in real-time
docker logs llm-router-app -f
```

## 🔐 SSL/HTTPS Setup

### Let's Encrypt (Production)

```bash
# Automatic setup
bash setup-ssl.sh

# Certificates will be:
# - Generated via Certbot
# - Auto-renewed twice daily
# - Linked to /root/llm-router/ssl/
```

### Self-Signed (Development)

```bash
# Generate certificates
bash generate-self-signed-ssl.sh

# Restart to enable HTTPS
docker-compose restart app
```

See [SSL_SETUP.md](SSL_SETUP.md) for detailed SSL configuration guide.

## 📁 Project Structure

```
llm-router/
├── app/                          # Application code
│   ├── main.py                  # FastAPI entry point
│   ├── config.py                # Config & key management
│   ├── database.py              # PostgreSQL connection
│   ├── translator.py            # Request/response translation
│   └── routers/                 # API routes
│       ├── admin.py            # Admin dashboard
│       ├── proxy.py            # LLM proxy endpoints
│       └── playground.py       # Chat playground
├── templates/                    # HTML templates
├── static/                      # Static assets
├── ssl/                         # SSL certificates
├── docker-compose.yml           # Docker services
├── Dockerfile                   # App container
├── requirements.txt             # Python dependencies
├── .env                         # Environment variables
├── deploy-to-server.sh          # Deployment script
├── setup-ssl.sh                 # SSL setup script
├── DOCKER_DEPLOYMENT.md         # Deployment guide
├── SSL_SETUP.md                 # SSL guide
└── README.md                    # This file
```

## 🐛 Troubleshooting

### App Won't Start

```bash
# Check logs
docker logs llm-router-app --tail 50

# Check database is ready
docker exec llm-router-db pg_isready -U llm_router_user

# Restart services
docker-compose restart
```

### Database Connection Issues

```bash
# Check PostgreSQL is running
docker ps | grep llm-router-db

# Check PostgreSQL logs
docker logs llm-router-db --tail 50

# Test connection
docker exec llm-router-app psql -h postgres -U llm_router_user -d llm_router -c "SELECT 1;"
```

### SSL Certificate Issues

```bash
# Check certificates exist
ls -la ssl/

# Verify certificate
openssl x509 -in ssl/cert.pem -text -noout

# Renew Let's Encrypt
certbot renew
```

### Port Conflicts

```bash
# Check what's using the port
netstat -tulpn | grep :4000

# Change port in docker-compose.yml if needed
```

## 📚 Documentation

- **[DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)** - Complete deployment guide with all commands
- **[SSL_SETUP.md](SSL_SETUP.md)** - SSL/HTTPS configuration (Let's Encrypt + self-signed)

## 🔒 Security Notes

1. **Change default passwords** in production
2. **Use Let's Encrypt** for trusted SSL certificates
3. **Keep API keys secure** - never commit to git
4. **Enable firewall rules** - only allow necessary ports
5. **Regular backups** - database and configuration
6. **Monitor logs** - check for suspicious activity

## 📈 Current Stats

- **Requests Processed**: 4,314
- **API Keys Managed**: 100
- **Failovers Handled**: 779
- **Data Migrated**: 4,433 rows

## 👤 Author

**Iyan Tama**
- Production Server: `root@178.128.59.20`
- Domain: `routers.iyantama.tech`

---

**Version**: 2.0.0 (Docker Edition)  
**Status**: ✅ Production Ready  
**Last Updated**: 2026-07-24
