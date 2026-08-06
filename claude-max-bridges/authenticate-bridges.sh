#!/bin/bash

# Authenticate all Claude Max Bridge instances
# Run this on the server (iyanserve@70.153.8.223)

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================="
echo "Claude Max Bridge - Authentication"
echo "========================================="
echo ""
echo "Each bridge needs a different Claude Max account."
echo ""

for i in {1..5}; do
    INSTANCE="$BRIDGE_DIR/bridge-$i"
    PORT=$((8787 + i - 1))

    echo "========================================="
    echo "Bridge $i (port $PORT)"
    echo "========================================="
    echo ""
    echo "You'll authenticate bridge-$i with a different Claude account."
    echo ""

    # Check if already authenticated
    if [ -d "$INSTANCE/.claude" ] || [ -f "$INSTANCE/oauth_tokens.json" ]; then
        echo "  ⚠️  bridge-$i appears to already be authenticated."
        read -p "  Re-authenticate? (y/N): " REAUTH
        if [[ ! "$REAUTH" =~ ^[Yy]$ ]]; then
            echo "  Skipping..."
            continue
        fi
    fi

    echo "  Steps:"
    echo "  1. Open a new terminal/SSH session"
    echo "  2. Run these commands:"
    echo ""
    echo "     cd $INSTANCE"
    echo "     HOME=\$(pwd) claude auth login --no-browser"
    echo ""
    echo "  3. Copy the URL and open in your browser"
    echo "  4. Login with your Claude Max account"
    echo "  5. Paste the authorization code back"
    echo ""
    read -p "  Press Enter after authenticating bridge-$i (or 's' to skip): " INPUT

    if [[ "$INPUT" == "s" || "$INPUT" == "S" ]]; then
        echo "  Skipped."
        continue
    fi

    # Verify authentication
    if [ -d "$INSTANCE/.claude" ]; then
        echo "  ✅ bridge-$i authenticated successfully!"
    else
        echo "  ⚠️  Authentication may not have completed. Check manually."
    fi

    echo ""
done

echo "========================================="
echo "Authentication Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Start all bridges:"
echo "   cd $BRIDGE_DIR"
echo "   bash manage-bridges.sh start"
echo ""
echo "2. Check status:"
echo "   bash manage-bridges.sh status"
echo ""
echo "3. Get API keys from logs:"
echo "   for i in {1..5}; do cat bridge-\$i/bridge.log | grep 'admin API key'; done"
echo ""
