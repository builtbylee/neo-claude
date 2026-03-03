#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "ERROR: CLOUDFLARE_API_TOKEN is required for non-interactive deploy." >&2
  exit 1
fi

if [[ -z "${NODE_EXTRA_CA_CERTS:-}" ]]; then
  echo "WARNING: NODE_EXTRA_CA_CERTS is not set. If your network uses a TLS-inspecting proxy, deploy may fail." >&2
fi

cd "$WEB_DIR"
npm run build:cf
npm run deploy
