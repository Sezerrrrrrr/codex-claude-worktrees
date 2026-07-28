#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: forge-kimi-worker.sh <worktree> <report-file>" >&2
  exit 2
fi

WORKTREE=$1
REPORT_FILE=$2
KIMI_HOME=${FORGE_KIMI_CODEX_HOME:-$HOME/.codex-kimi}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPACTION_PROMPT="$SCRIPT_DIR/../references/compaction-prompt.md"

if [ ! -f "$KIMI_HOME/config.toml" ]; then
  echo "Kimi Codex home is not configured: $KIMI_HOME/config.toml" >&2
  echo "Configure an isolated Kimi provider through CC Switch, or set FORGE_KIMI_CODEX_HOME." >&2
  exit 3
fi

KIMI_MODEL=${FORGE_KIMI_MODEL:-$(python3 - "$KIMI_HOME/config.toml" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as handle:
    config = tomllib.load(handle)
print(config.get("model", ""))
PY
)}

if [ -z "$KIMI_MODEL" ]; then
  echo "Kimi Codex home does not declare a model: $KIMI_HOME/config.toml" >&2
  exit 3
fi

mkdir -p "$(dirname "$REPORT_FILE")"
CODEX_HOME="$KIMI_HOME" exec codex exec \
  -C "$WORKTREE" \
  -c 'model_provider="custom"' \
  -c 'model_reasoning_effort="xhigh"' \
  -c 'model_context_window=262144' \
  -c 'model_auto_compact_token_limit=104858' \
  -c 'model_auto_compact_token_limit_scope="total"' \
  -c "experimental_compact_prompt_file=\"$COMPACTION_PROMPT\"" \
  -m "$KIMI_MODEL" \
  -s workspace-write \
  -o "$REPORT_FILE" \
  -
