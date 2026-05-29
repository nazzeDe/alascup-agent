#!/usr/bin/env bash
# ============================================================
# 一键构建所有容器镜像 (原生 Docker，无需 docker compose)
# 适用于无网络环境，所有基础镜像需预先 load 到本地
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---------- 镜像 tag 配置 ----------
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-}"

# 基础镜像 (需预先 docker load)
BASE_PYTHON="ghcr.io/loong64/python:3.13.13-slim-trixie"
BASE_NGINX="ghcr.io/loong64/nginx:1.29.8-debian-perl"
BASE_POSTGRES="ghcr.io/loong64/postgres:18-trixie"

FRONTEND_IMAGE="${REGISTRY}alascup-frontend:${TAG}"
WEB_SERVER_IMAGE="${REGISTRY}alascup-web-server:${TAG}"
TOOL_SERVER_IMAGE="${REGISTRY}alascup-tool-server:${TAG}"
RAG_SERVER_IMAGE="${REGISTRY}alascup-rag-server:${TAG}"

# ---------- 预检 ----------
check_base_image() {
    local img="$1"
    if ! docker image inspect "$img" &>/dev/null; then
        echo "[ERROR] 基础镜像不存在: $img"
        echo "  请先通过 docker load < ${img##*/}.tar 导入"
        exit 1
    fi
}

echo "==> 检查基础镜像..."
check_base_image "$BASE_PYTHON"
check_base_image "$BASE_NGINX"
check_base_image "$BASE_POSTGRES"
echo "    基础镜像就绪"

# ---------- 构建前端 ----------
echo "==> 构建前端静态文件..."
cd "$PROJECT_DIR/frontend"
if [ ! -d "vue-project/dist" ]; then
    if [ -f "vue-project/package.json" ]; then
        echo "    运行 npm build..."
        cd vue-project
        npm install --prefer-offline && npm run build
        cd ..
    else
        echo "[ERROR] vue-project/dist/ 不存在且无法自动构建"
        exit 1
    fi
fi
echo "    前端构建产物就绪"

# ---------- Docker 构建 ----------
build_image() {
    local name="$1"
    local context="$2"
    local dockerfile="${3:-Dockerfile}"
    echo "==> 构建镜像: $name"
    docker build \
        --platform linux/loong64 \
        -t "$name" \
        -f "$context/$dockerfile" \
        "$context"
    echo "    完成: $name"
}

cd "$PROJECT_DIR"

build_image "$TOOL_SERVER_IMAGE"  "$PROJECT_DIR/tool-server"
build_image "$RAG_SERVER_IMAGE"   "$PROJECT_DIR/rag-server"
build_image "$WEB_SERVER_IMAGE"   "$PROJECT_DIR/web-server"
build_image "$FRONTEND_IMAGE"     "$PROJECT_DIR/frontend"

# ---------- 导出 (可选) ----------
if [ "${EXPORT:-0}" = "1" ]; then
    EXPORT_DIR="$PROJECT_DIR/docker-images"
    mkdir -p "$EXPORT_DIR"
    echo "==> 导出镜像到 $EXPORT_DIR ..."
    for img in "$FRONTEND_IMAGE" "$WEB_SERVER_IMAGE" "$TOOL_SERVER_IMAGE" "$RAG_SERVER_IMAGE"; do
        fname="${img//[:\/]/_}.tar"
        echo "    导出: $fname"
        docker save -o "$EXPORT_DIR/$fname" "$img"
    done
    echo "    导出完成"
fi

echo ""
echo "========== 构建完毕 =========="
echo "  前端:        $FRONTEND_IMAGE"
echo "  Web Server:  $WEB_SERVER_IMAGE"
echo "  Tool Server: $TOOL_SERVER_IMAGE"
echo "  RAG Server:  $RAG_SERVER_IMAGE"
echo "  Postgres:    使用预置镜像 $BASE_POSTGRES"
