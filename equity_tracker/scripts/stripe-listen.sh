#!/usr/bin/env bash
set -euo pipefail

# If we don't have a Stripe key, print a message and exit cleanly.
if [ -z "${STRIPE_SECRET_KEY:-}" ]; then
    echo "STRIPE_SECRET_KEY is not set; skipping the Stripe webhook listener."
    exit 0
fi

# Start the Stripe CLI webhook listener and forward events to Django.
exec stripe listen \
    --api-key "${STRIPE_SECRET_KEY}" \
    --forward-to "${STRIPE_FORWARD_TO:-web:8000/stripe/webhook/}"
