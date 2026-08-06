#!/bin/bash

# Setup Multiple Claude Max Bridge Instances
# Each instance runs on different port with separate Claude auth

set -e

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="$BRIDGE_DIR/bridge-template"

echo "========================================="
echo "Claude Max Bridge - Multi-Instance Setup"
echo "========================================="
echo ""

# Ask how many instances
read -p "How many Claude Max accounts do you have? (1-10): " NUM_INSTANCES

if [ -z "$NUM_INSTANCES" ] || [ "$NUM_INSTANCES" -lt 1 ] || [ "$NUM_INSTANCES" -gt 10 ]; then
    echo "Invalid number. Must be between 1 and 10."
    exit 1
fi

echo ""
echo "Creating $NUM_INSTANCES bridge instances..."
echo ""

BASE_PORT=8787

for i in $(seq 1 $NUM_INSTANCES); do
    INSTANCE_NAME="bridge-$i"
    INSTANCE_DIR="$BRIDGE_DIR/$INSTANCE_NAME"
    PORT=$((BASE_PORT + i - 1))

    echo "[$i/$NUM_INSTANCES] Setting up $INSTANCE_NAME on port $PORT..."

    # Create instance directory
    mkdir -p "$INSTANCE_DIR"

    # Copy template files
    cp "$TEMPLATE_DIR/main.py" "$INSTANCE_DIR/"
    cp "$TEMPLATE_DIR/requirements.txt" "$INSTANCE_DIR/"
    cp -r "$TEMPLATE_DIR/bridge" "$INSTANCE_DIR/" 2>/dev/null || true

    # Create .env file
    cat > "$INSTANCE_DIR/.env" << EOF
# Claude Max Bridge Instance $i
PORT=$PORT
DEFAULT_MODEL=opus

# Rate Limiting
RATE_LIMIT_RPM=30
RATE_LIMIT_TPM=100000

# Files
API_KEYS_FILE=api_keys.json
OAUTH_USERS_FILE=oauth_users.json
CONFIG_FILE=config.json

# OAuth Master User
OAUTH_MASTER_USER=admin
OAUTH_MASTER_PASSWORD=admin_pass_$i
EOF

    # Create Python venv
    echo "  - Creating Python virtual environment..."
    cd "$INSTANCE_DIR"
    python -m venv .venv

    # Install dependencies
    echo "  - Installing dependencies..."
    if [ -f ".venv/Scripts/activate" ]; then
        .venv/Scripts/python -m pip install --upgrade pip
        .venv/Scripts/python -m pip install -q fastapi "uvicorn[standard]" python-dotenv pydantic python-multipart
    else
        source .venv/bin/activate
        pip install --upgrade pip
        pip install -q fastapi "uvicorn[standard]" python-dotenv pydantic python-multipart
        deactivate
    fi

    # Create start script
    cat > "$INSTANCE_DIR/start.sh" << 'STARTEOF'
#!/bin/bash
cd "$(dirname "$0")"
if [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
else
    source .venv/bin/activate
fi
python main.py
STARTEOF
    chmod +x "$INSTANCE_DIR/start.sh"

    # Create README for this instance
    cat > "$INSTANCE_DIR/README.md" << READMEEOF
# Claude Max Bridge - Instance $i

**Port:** $PORT
**Status:** Not authenticated yet

## Setup

### 1. Authenticate Claude CLI

Each instance needs its own Claude account. Use a separate terminal session:

\`\`\`bash
# Set HOME to instance directory (Linux/Mac)
export HOME="$INSTANCE_DIR"
claude auth login

# Or for Windows (Git Bash)
HOME="$INSTANCE_DIR" claude auth login --no-browser
\`\`\`

### 2. Start Bridge

\`\`\`bash
cd "$INSTANCE_DIR"
./start.sh
\`\`\`

### 3. Get Admin API Key

On first run, the bridge prints:

\`\`\`
🔑 Default admin API key: sk-mb-admin-...
\`\`\`

Save this key!

## Usage

\`\`\`bash
# Test endpoint
curl http://localhost:$PORT/v1/models \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Chat completion
curl http://localhost:$PORT/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "opus",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
\`\`\`

## Admin Dashboard

- List keys: \`GET http://localhost:$PORT/v1/admin/keys\`
- Create key: \`POST http://localhost:$PORT/v1/admin/keys\`
- Stats: \`GET http://localhost:$PORT/v1/admin/stats\`
READMEEOF

    echo "  ✅ $INSTANCE_NAME created"
    echo ""
done

echo "========================================="
echo "✅ Setup Complete!"
echo "========================================="
echo ""
echo "Created $NUM_INSTANCES bridge instances:"
echo ""

for i in $(seq 1 $NUM_INSTANCES); do
    PORT=$((BASE_PORT + i - 1))
    echo "  bridge-$i → http://localhost:$PORT"
done

echo ""
echo "Next Steps:"
echo ""
echo "1. Authenticate each instance with a different Claude account:"
echo "   cd claude-max-bridges/bridge-1"
echo "   HOME=\$(pwd) claude auth login"
echo ""
echo "2. Start each bridge:"
echo "   cd claude-max-bridges/bridge-1"
echo "   ./start.sh"
echo ""
echo "3. Integrate with main router (add to .env):"
echo "   CLAUDE_BRIDGE_1_URL=http://localhost:8787"
echo "   CLAUDE_BRIDGE_2_URL=http://localhost:8788"
echo "   # etc..."
echo ""
