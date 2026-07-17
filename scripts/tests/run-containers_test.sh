#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/../run-containers.sh"

grep -Fq 'FRONTEND_HOST_PORT="${FRONTEND_HOST_PORT:-18080}"' "$SCRIPT" || {
    echo "FAIL: frontend default host port must be 18080" >&2
    exit 1
}

grep -Fq 'TOOLSERVER_HOST_EXEC=$TOOLSERVER_HOST_EXEC' "$SCRIPT" || {
    echo "FAIL: tool-server host execution mode must be configurable" >&2
    exit 1
}

grep -Fq 'TOOLSERVER_CAP_ARGS+=(--cap-add SYS_CHROOT)' "$SCRIPT" || {
    echo "FAIL: chroot mode must add SYS_CHROOT" >&2
    exit 1
}

if grep -Fq 'if [ "${CLEAN:-1}" = "1" ]; then' "$SCRIPT"; then
    echo "FAIL: container cleanup must run on every invocation" >&2
    exit 1
fi

cleanup_line="$(grep -n '^cleanup_old$' "$SCRIPT" | head -n 1 | cut -d: -f1)"
postgres_line="$(grep -n '^# ---------- 启动 postgres' "$SCRIPT" | head -n 1 | cut -d: -f1)"

if [ -z "$cleanup_line" ] || [ -z "$postgres_line" ] || [ "$cleanup_line" -gt "$postgres_line" ]; then
    echo "FAIL: old containers must be cleaned before starting PostgreSQL" >&2
    exit 1
fi

echo "PASS: run-containers startup policy"
