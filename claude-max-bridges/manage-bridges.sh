#!/bin/bash

# Manage all Claude Max Bridge instances
# Usage: ./manage-bridges.sh [start|stop|restart|status|logs]

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"

# Find all bridge instances
INSTANCES=($(ls -d "$BRIDGE_DIR"/bridge-* 2>/dev/null | grep -v "bridge-template" | sort))

if [ ${#INSTANCES[@]} -eq 0 ]; then
    echo "No bridge instances found."
    echo "Run: bash setup-multi-instance.sh"
    exit 1
fi

ACTION="${1:-status}"

case "$ACTION" in
    start)
        echo "Starting all bridge instances..."
        for instance in "${INSTANCES[@]}"; do
            name=$(basename "$instance")
            port=$(grep "^PORT=" "$instance/.env" | cut -d= -f2)

            # Check if already running
            if lsof -ti:$port >/dev/null 2>&1 || netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING >/dev/null; then
                echo "  ⚠️  $name (port $port) - already running"
                continue
            fi

            echo "  ▶️  Starting $name on port $port..."
            cd "$instance"

            # Start in background
            if [ -f ".venv/Scripts/activate" ]; then
                (.venv/Scripts/python main.py >> bridge.log 2>&1 &)
            else
                (source .venv/bin/activate && python main.py >> bridge.log 2>&1 &)
            fi

            sleep 2

            # Verify started
            if lsof -ti:$port >/dev/null 2>&1 || netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING >/dev/null; then
                echo "  ✅ $name started"
            else
                echo "  ❌ $name failed to start (check $instance/bridge.log)"
            fi
        done
        echo ""
        echo "Done! Check status with: $0 status"
        ;;

    stop)
        echo "Stopping all bridge instances..."
        for instance in "${INSTANCES[@]}"; do
            name=$(basename "$instance")
            port=$(grep "^PORT=" "$instance/.env" | cut -d= -f2)

            # Find PID using port
            if command -v lsof >/dev/null 2>&1; then
                PID=$(lsof -ti:$port 2>/dev/null)
            else
                # Windows fallback using netstat
                PID=$(netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING | awk '{print $5}' | head -1)
            fi

            if [ -z "$PID" ]; then
                echo "  ⚠️  $name (port $port) - not running"
            else
                echo "  ⏹️  Stopping $name (PID: $PID)..."
                kill $PID 2>/dev/null || taskkill //PID $PID //F 2>/dev/null
                sleep 1
                echo "  ✅ $name stopped"
            fi
        done
        ;;

    restart)
        echo "Restarting all bridge instances..."
        $0 stop
        sleep 2
        $0 start
        ;;

    status)
        echo "========================================="
        echo "Claude Max Bridge Instances Status"
        echo "========================================="
        echo ""

        for instance in "${INSTANCES[@]}"; do
            name=$(basename "$instance")
            port=$(grep "^PORT=" "$instance/.env" | cut -d= -f2)

            # Check if running
            if lsof -ti:$port >/dev/null 2>&1 || netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING >/dev/null; then
                if command -v lsof >/dev/null 2>&1; then
                    PID=$(lsof -ti:$port 2>/dev/null)
                else
                    PID=$(netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING | awk '{print $5}' | head -1)
                fi

                # Test endpoint
                if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/health" 2>/dev/null | grep -q "200"; then
                    echo "✅ $name - RUNNING (port $port, PID: $PID)"
                else
                    echo "⚠️  $name - PORT OPEN but not responding (port $port, PID: $PID)"
                fi
            else
                # Check if authenticated
                if [ -d "$instance/.claude" ] || [ -f "$instance/oauth_tokens.json" ]; then
                    echo "⏹️  $name - STOPPED (port $port, authenticated)"
                else
                    echo "❌ $name - STOPPED (port $port, NOT authenticated)"
                fi
            fi
        done

        echo ""
        echo "Total instances: ${#INSTANCES[@]}"
        ;;

    logs)
        INSTANCE_NUM="${2:-1}"
        INSTANCE="${INSTANCES[$((INSTANCE_NUM-1))]}"

        if [ -z "$INSTANCE" ]; then
            echo "Invalid instance number: $INSTANCE_NUM"
            echo "Available: 1-${#INSTANCES[@]}"
            exit 1
        fi

        name=$(basename "$INSTANCE")
        echo "Logs for $name:"
        echo "========================================="

        if [ -f "$INSTANCE/bridge.log" ]; then
            tail -f "$INSTANCE/bridge.log"
        else
            echo "No log file found at $INSTANCE/bridge.log"
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|restart|status|logs [instance_num]}"
        echo ""
        echo "Examples:"
        echo "  $0 start          # Start all bridges"
        echo "  $0 stop           # Stop all bridges"
        echo "  $0 restart        # Restart all bridges"
        echo "  $0 status         # Show status of all bridges"
        echo "  $0 logs 1         # Tail logs for bridge-1"
        exit 1
        ;;
esac
