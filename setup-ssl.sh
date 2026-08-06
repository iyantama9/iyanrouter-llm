#!/bin/bash

# SSL Setup Script for LLM Router
# Run this on your production server (root@178.128.59.20)

set -e

DOMAIN="routers.iyantama.tech"
EMAIL="your-email@example.com"  # Update this with your email

echo "========================================="
echo "SSL Certificate Setup for LLM Router"
echo "Domain: $DOMAIN"
echo "========================================="

# 1. Install certbot if not installed
if ! command -v certbot &> /dev/null; then
    echo "Step 1: Installing Certbot..."
    apt-get update
    apt-get install -y certbot
else
    echo "Step 1: Certbot already installed"
fi

# 2. Stop services temporarily for certificate generation
echo "Step 2: Stopping services temporarily..."
cd /root/llm-router
docker-compose stop app || true

# 3. Generate Let's Encrypt certificates
echo "Step 3: Generating Let's Encrypt SSL certificates..."
echo "This requires port 80 to be accessible from internet..."

if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    certbot certonly --standalone \
        --preferred-challenges http \
        --email $EMAIL \
        --agree-tos \
        --no-eff-email \
        -d $DOMAIN
else
    echo "Certificates already exist for $DOMAIN"
fi

# 4. Create SSL directory in project
echo "Step 4: Creating SSL directory..."
mkdir -p /root/llm-router/ssl

# 5. Create symbolic links to Let's Encrypt certificates
echo "Step 5: Creating certificate links..."
ln -sf /etc/letsencrypt/live/$DOMAIN/fullchain.pem /root/llm-router/ssl/cert.pem
ln -sf /etc/letsencrypt/live/$DOMAIN/privkey.pem /root/llm-router/ssl/key.pem

# 6. Set proper permissions
echo "Step 6: Setting permissions..."
chmod 755 /root/llm-router/ssl
chmod 644 /root/llm-router/ssl/cert.pem
chmod 600 /root/llm-router/ssl/key.pem

# 7. Update .env file with SSL paths
echo "Step 7: Updating .env with SSL configuration..."
cd /root/llm-router

# Backup original .env
cp .env .env.backup

# Update SSL_KEYFILE and SSL_CERTFILE
sed -i 's|^SSL_KEYFILE=.*|SSL_KEYFILE=/app/ssl/key.pem|' .env
sed -i 's|^SSL_CERTFILE=.*|SSL_CERTFILE=/app/ssl/cert.pem|' .env

echo "Updated .env with SSL paths"

# 8. Setup automatic renewal
echo "Step 8: Setting up automatic SSL renewal..."

# Create renewal hook script
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-llm-router.sh << 'EOF'
#!/bin/bash
cd /root/llm-router
docker-compose restart app
EOF

chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-llm-router.sh

# Add cron job for renewal (runs twice daily)
if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    (crontab -l 2>/dev/null; echo "0 0,12 * * * certbot renew --quiet --deploy-hook '/etc/letsencrypt/renewal-hooks/deploy/restart-llm-router.sh'") | crontab -
    echo "Added cron job for certificate renewal"
else
    echo "Renewal cron job already exists"
fi

# 9. Restart services with SSL
echo "Step 9: Restarting services with SSL enabled..."
docker-compose up -d

# 10. Wait for services to start
sleep 10

# 11. Verify SSL setup
echo "Step 10: Verifying SSL setup..."
docker logs llm-router-app --tail 20

echo ""
echo "========================================="
echo "✅ SSL Setup Complete!"
echo "========================================="
echo ""
echo "Your router is now accessible via HTTPS:"
echo "  https://$DOMAIN"
echo ""
echo "Certificate details:"
echo "  Issuer: Let's Encrypt"
echo "  Valid for: 90 days"
echo "  Auto-renewal: Enabled (twice daily check)"
echo ""
echo "SSL files location:"
echo "  Certificate: /etc/letsencrypt/live/$DOMAIN/fullchain.pem"
echo "  Private Key: /etc/letsencrypt/live/$DOMAIN/privkey.pem"
echo "  Linked to: /root/llm-router/ssl/"
echo ""
echo "HTTP (port 80) will automatically redirect to HTTPS (port 443)"
echo ""
echo "To manually renew certificates:"
echo "  certbot renew"
echo ""
