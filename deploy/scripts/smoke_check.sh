#!/usr/bin/env bash
# Post-deploy smoke checks.
# Runs from VPS, uses public URLs.

set -euo pipefail

cd "$(dirname "$0")/../.."

# Load env so we can reference PUBLIC_HOST_*
set -a
# shellcheck disable=SC1091
source .env
set +a

PUBLIC_INBOX="https://${PUBLIC_HOST_INBOX}"
PUBLIC_ADMIN="https://${PUBLIC_HOST_ADMIN}"

echo "Checking $PUBLIC_INBOX/health..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$PUBLIC_INBOX/health" || echo "000")
if [ "$HTTP" != "200" ]; then
    echo "FAIL: /health returned $HTTP"
    exit 1
fi

echo "Checking $PUBLIC_INBOX/ready/quick..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$PUBLIC_INBOX/ready/quick" || echo "000")
if [ "$HTTP" != "200" ]; then
    echo "FAIL: /ready/quick returned $HTTP"
    exit 1
fi

echo "Checking $PUBLIC_INBOX/ready (full)..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$PUBLIC_INBOX/ready" || echo "000")
if [ "$HTTP" != "200" ]; then
    echo "WARN: /ready returned $HTTP (worker heartbeat may not be ready yet)"
fi

echo "Checking $PUBLIC_ADMIN..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" -L "$PUBLIC_ADMIN" || echo "000")
if [ "$HTTP" -lt 200 ] || [ "$HTTP" -ge 500 ]; then
    echo "FAIL: admin dashboard returned $HTTP"
    exit 1
fi

echo "Checking webhook GET (verification challenge)..."
RESP=$(curl -sS "$PUBLIC_INBOX/webhooks/sendpulse?hub.challenge=test123" || echo "")
if ! echo "$RESP" | grep -q "test123"; then
    echo "WARN: webhook verification did not echo challenge: $RESP"
fi

echo "All smoke checks passed."
