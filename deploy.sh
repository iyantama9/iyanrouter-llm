#!/bin/bash

# LLM Router - Server Deployment Script
# Run this on your production server (root@178.128.59.20)

set -e

echo "========================================="
echo "LLM Router - Docker Deployment"
echo "========================================="

# 1. Update system
echo "Step 1: Updating system packages..."
apt-get update
apt-get upgrade -y

# 2. Install Docker (if not installed)
if ! command -v docker &> /dev/null; then
    echo "Step 2: Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
else
    echo "Step 2: Docker already installed"
fi

# 3. Install Docker Compose (if not installed)
if ! command -v docker-compose &> /dev/null; then
    echo "Step 3: Installing Docker Compose..."
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
else
    echo "Step 3: Docker Compose already installed"
fi

# 4. Navigate to project directory
echo "Step 4: Navigating to project directory..."
cd /root/llm-router

# 5. Stop existing services if running
echo "Step 5: Stopping existing services..."
docker-compose down || true

# 6. Build and start services
echo "Step 6: Building and starting Docker services..."
docker-compose build
docker-compose up -d

# 7. Wait for services to be healthy
echo "Step 7: Waiting for services to be healthy..."
sleep 15

# 8. Verify deployment
echo "Step 8: Verifying deployment..."
docker ps | grep llm-router

echo ""
echo "========================================="
echo "✅ Deployment Complete!"
echo "========================================="
echo ""
echo "Services running:"
docker-compose ps
echo ""
echo "Access your router at:"
echo "  http://178.128.59.20:4000"
echo ""
echo "View logs:"
echo "  docker-compose logs -f"
echo ""
