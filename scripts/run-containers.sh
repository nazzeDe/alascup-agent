#!/usr/bin/env bash
# ============================================================
# 一键运行所有容器 (原生 Docker，无需 docker compose)
# 自动创建网络、卷、日志目录，按依赖顺序启动
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---------- 配置 ----------
TAG="${TAG:-latest}"
NETWORK="${NETWORK:-alascup-net}"
RESTART="${RESTART:-unless-stopped}"

# 镜像
BASE_POSTGRES="ghcr.io/loong64/postgres:18-trixie"
FRONTEND_IMAGE="alascup-frontend:${TAG}"
WEB_SERVER_IMAGE="alascup-web-server:${TAG}"
TOOL_SERVER_IMAGE="alascup-tool-server:${TAG}"

# 容器名
POSTGRES_CONTAINER="postgres"
TOOL_SERVER_CONTAINER="tool-server"
WEB_SERVER_CONTAINER="web-server"
FRONTEND_CONTAINER="alascup-frontend"

# ---------- 清理旧容器 (可选) ----------
cleanup_old() {
    local containers=("$POSTGRES_CONTAINER" "$TOOL_SERVER_CONTAINER" "$WEB_SERVER_CONTAINER" "$FRONTEND_CONTAINER")
    for c in "${containers[@]}"; do
        if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
            echo "==> 移除旧容器: $c"
            docker rm -f "$c" 2>/dev/null || true
        fi
    done
}

if [ "${CLEAN:-0}" = "1" ]; then
    cleanup_old
fi

# ---------- 创建网络 ----------
if ! docker network inspect "$NETWORK" &>/dev/null; then
    echo "==> 创建 Docker 网络: $NETWORK"
    docker network create --driver bridge "$NETWORK"
else
    echo "==> 网络已存在: $NETWORK"
fi

# ---------- 创建卷 ----------
create_volume() {
    if ! docker volume inspect "$1" &>/dev/null; then
        echo "==> 创建卷: $1"
        docker volume create "$1"
    fi
}
create_volume "pgdata"

# ---------- 创建日志目录 ----------
mkdir -p "$PROJECT_DIR/logs/web-server"
mkdir -p "$PROJECT_DIR/logs/tool-server"

# ---------- 启动 postgres ----------
echo "==> 启动 PostgreSQL..."
docker run -d \
    --name "$POSTGRES_CONTAINER" \
    --network "$NETWORK" \
    --restart "$RESTART" \
    --platform linux/loong64 \
    -e POSTGRES_USER=alascup \
    -e POSTGRES_PASSWORD=alascup \
    -e POSTGRES_DB=alascup \
    -v "pgdata:/var/lib/postgresql/data:Z" \
    --tmpfs /tmp:size=64m,mode=1777 \
    --tmpfs /run/postgresql:size=16m,mode=1777 \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add SETGID \
    --cap-add SETUID \
    --pids-limit 128 \
    --memory 512m \
    --cpus 1.00 \
    "$BASE_POSTGRES"

# ---------- 启动 tool-server ----------
echo "==> 启动 Tool Server..."
docker run -d \
    --name "$TOOL_SERVER_CONTAINER" \
    --network "$NETWORK" \
    --restart "$RESTART" \
    --platform linux/loong64 \
    -e TOOLSERVER_HOST_EXEC=nsenter \
    -v "/var/log:/host/var/log:ro,Z" \
    -v "/proc:/host/proc:ro,Z" \
    -v "/sys:/host/sys:ro,Z" \
    -v "$PROJECT_DIR/logs/tool-server:/app/logs:Z" \
    --read-only \
    --tmpfs /tmp:size=128m,mode=1777 \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --cap-add SYS_PTRACE \
    --cap-add KILL \
    --cap-add NET_ADMIN \
    --cap-add NET_RAW \
    --cap-add DAC_READ_SEARCH \
    --cap-add SYS_ADMIN \
    --cap-add SYSLOG \
    --pid host \
    --pids-limit 512 \
    --memory 768m \
    --cpus 1.00 \
    "$TOOL_SERVER_IMAGE"

# ---------- 等待 postgres 就绪 ----------
echo "==> 等待 PostgreSQL 就绪..."
for i in $(seq 1 30); do
    if docker exec "$POSTGRES_CONTAINER" pg_isready -U alascup &>/dev/null; then
        echo "    PostgreSQL 就绪"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "[ERROR] PostgreSQL 启动超时"
        exit 1
    fi
    sleep 1
done

# ---------- 等待 tool-server 就绪 ----------
echo "==> 等待 Tool Server 就绪..."
for i in $(seq 1 15); do
    if docker exec "$TOOL_SERVER_CONTAINER" curl -sf http://localhost:11451/health &>/dev/null; then
        echo "    Tool Server 就绪"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "[WARN] Tool Server health check 超时，继续启动..."
    fi
    sleep 2
done

# ---------- 启动 web-server ----------
echo "==> 启动 Web Server..."
docker run -d \
    --name "$WEB_SERVER_CONTAINER" \
    --network "$NETWORK" \
    --restart "$RESTART" \
    --platform linux/loong64 \
    --workdir /app \
    -e HOST=0.0.0.0 \
    -e PORT=11450 \
    -e DATABASE_URL=postgresql+asyncpg://alascup:alascup@postgres:5432/alascup \
    -v "$PROJECT_DIR/web-server/config:/app/config:ro,Z" \
    -v "$PROJECT_DIR/logs/web-server:/app/logs:Z" \
    --read-only \
    --tmpfs /tmp:size=64m,mode=1777 \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --pids-limit 256 \
    --memory 512m \
    --cpus 0.50 \
    -p 11451:11451 \
    "$WEB_SERVER_IMAGE"

# ---------- 等待 web-server 就绪 ----------
echo "==> 等待 Web Server 就绪..."
for i in $(seq 1 15); do
    if docker exec "$WEB_SERVER_CONTAINER" curl -sf http://localhost:11450/health &>/dev/null; then
        echo "    Web Server 就绪"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "[WARN] Web Server health check 超时，继续启动..."
    fi
    sleep 2
done

# ---------- 启动 frontend ----------
echo "==> 启动前端..."
docker run -d \
    --name "$FRONTEND_CONTAINER" \
    --network "$NETWORK" \
    --restart "$RESTART" \
    --platform linux/loong64 \
    -p 80:80 \
    "$FRONTEND_IMAGE"

echo ""
echo "========== 全部容器已启动 =========="
docker ps --filter "network=$NETWORK" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
