#!/usr/bin/env bash
set -euo pipefail

SOURCE_HOME="${FORGE_KIMI_SOURCE_CODEX_HOME:-$HOME/.codex-kimi-router}"
TARGET_HOME="${FORGE_KIMI_CODEX_HOME:-$HOME/.codex-kimi}"
SOURCE_CONFIG="$SOURCE_HOME/config.toml"
SOURCE_AUTH="$SOURCE_HOME/auth.json"

if [ ! -f "$SOURCE_CONFIG" ]; then
  printf 'forge-kimi-bootstrap: active Codex config not found: %s\n' "$SOURCE_CONFIG" >&2
  exit 1
fi

MODEL_NAME="$(sed -nE 's/^[[:space:]]*model[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$SOURCE_CONFIG" | head -n 1)"
CATALOG_NAME="$(sed -nE 's/^[[:space:]]*model_catalog_json[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$SOURCE_CONFIG" | head -n 1)"
PROXY_BASE_URL="$(sed -nE 's/^[[:space:]]*base_url[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$SOURCE_CONFIG" | head -n 1)"

if ! printf '%s\n' "$MODEL_NAME" | grep -Eiq 'kimi|moonshot'; then
  printf '%s\n' 'forge-kimi-bootstrap: refusing snapshot because the active Codex config does not mention Kimi or Moonshot.' >&2
  printf '%s\n' 'Point CC Switch at ~/.codex-kimi-router, enable the Kimi provider, then retry.' >&2
  exit 1
fi

case "$PROXY_BASE_URL" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    printf 'forge-kimi-bootstrap: expected a local CC Switch proxy URL, got: %s\n' "$PROXY_BASE_URL" >&2
    exit 1
    ;;
esac

case "$CATALOG_NAME" in
  ""|*/*)
    printf 'forge-kimi-bootstrap: expected a local model catalog filename, got: %s\n' "$CATALOG_NAME" >&2
    exit 1
    ;;
esac

SOURCE_CATALOG="$SOURCE_HOME/$CATALOG_NAME"
if [ ! -f "$SOURCE_CATALOG" ]; then
  printf 'forge-kimi-bootstrap: referenced model catalog not found: %s\n' "$SOURCE_CATALOG" >&2
  exit 1
fi

if [ ! -f "$SOURCE_AUTH" ]; then
  printf 'forge-kimi-bootstrap: active Kimi auth not found: %s\n' "$SOURCE_AUTH" >&2
  exit 1
fi

mkdir -p "$TARGET_HOME"
TEMP_CONFIG="$(mktemp "$TARGET_HOME/config.toml.tmp.XXXXXX")"
trap 'rm -f "$TEMP_CONFIG"' EXIT

cat >"$TEMP_CONFIG" <<EOF
model_provider = "custom"
model = "$MODEL_NAME"
model_catalog_json = "$CATALOG_NAME"
disable_response_storage = true
model_reasoning_effort = "xhigh"
model_context_window = 262144
model_auto_compact_token_limit = 104858
model_auto_compact_token_limit_scope = "total"

[model_providers.custom]
name = "Kimi via isolated CC Switch proxy"
base_url = "$PROXY_BASE_URL"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
EOF

install -m 0600 "$TEMP_CONFIG" "$TARGET_HOME/config.toml"
install -m 0600 "$SOURCE_CATALOG" "$TARGET_HOME/$CATALOG_NAME"
install -m 0600 "$SOURCE_AUTH" "$TARGET_HOME/auth.json"

cat >"$TARGET_HOME/forge-provider.txt" <<EOF
Created by forge-kimi-bootstrap.sh
Source: $SOURCE_HOME
Provider marker: Kimi/Moonshot
CC Switch must remain on Kimi with local routing enabled.
Refresh this snapshot after changing the Kimi provider in CC Switch.
EOF
chmod 0600 "$TARGET_HOME/forge-provider.txt"

printf 'Forge Kimi CODEX_HOME ready: %s\n' "$TARGET_HOME"
printf '%s\n' 'Leave CC Switch on Kimi with local routing enabled, then run Forge doctor.'
