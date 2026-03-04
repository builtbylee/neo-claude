#!/usr/bin/env bash
set -euo pipefail

resolve_certifi_bundle() {
  local py="$1"
  if [[ ! -x "$py" ]] && ! command -v "$py" >/dev/null 2>&1; then
    return 1
  fi
  "$py" - <<'PY' 2>/dev/null || return 1
try:
    import certifi
    print(certifi.where())
except Exception:
    raise SystemExit(1)
PY
}

if [[ -z "${NODE_EXTRA_CA_CERTS:-}" ]]; then
  for candidate in "../.venv/bin/python" "python3" "/usr/bin/python3"; do
    cert_path="$(resolve_certifi_bundle "$candidate" || true)"
    if [[ -n "${cert_path:-}" && -f "$cert_path" ]]; then
      export NODE_EXTRA_CA_CERTS="$cert_path"
      break
    fi
  done
fi

exec npx wrangler "$@"
