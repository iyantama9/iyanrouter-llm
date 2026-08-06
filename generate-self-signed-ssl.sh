#!/bin/bash

# Generate Self-Signed SSL Certificates for Local Testing
# Use this for development/testing before deploying with Let's Encrypt

set -e

DOMAIN="localhost"
DAYS=365

echo "========================================="
echo "Generating Self-Signed SSL Certificates"
echo "Domain: $DOMAIN"
echo "Valid for: $DAYS days"
echo "========================================="

# Create ssl directory
mkdir -p ssl

# Generate private key and certificate
openssl req -x509 -nodes -days $DAYS \
    -newkey rsa:2048 \
    -keyout ssl/key.pem \
    -out ssl/cert.pem \
    -subj "/C=ID/ST=Jakarta/L=Jakarta/O=LLM Router/CN=$DOMAIN"

# Set proper permissions
chmod 600 ssl/key.pem
chmod 644 ssl/cert.pem

echo ""
echo "✅ Self-signed certificates generated!"
echo ""
echo "Files created:"
echo "  - ssl/key.pem (private key)"
echo "  - ssl/cert.pem (certificate)"
echo ""
echo "To use these certificates:"
echo "1. Make sure .env has:"
echo "   SSL_KEYFILE=/app/ssl/key.pem"
echo "   SSL_CERTFILE=/app/ssl/cert.pem"
echo ""
echo "2. Restart services:"
echo "   docker-compose restart app"
echo ""
echo "3. Access via HTTPS:"
echo "   https://localhost:4000"
echo ""
echo "⚠️  WARNING: Self-signed certificates will show security warnings"
echo "    in browsers. Use Let's Encrypt for production!"
echo ""
