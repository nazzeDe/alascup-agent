#!/usr/bin/env bash
# ============================================================
# 根据 Dockerfile 构建所有应用镜像
# 用法: ./build-images.sh [镜像名...]
#   无参   → 构建全部
#   有参   → 仅构建指定镜像 (如 ./build-images.sh tool-server frontend)
# ============================================================
set -euo pipefail

source "$(dirname "$0")/build-config.sh"

# ---------- 确保基础镜像存在 (龙芯需预拉 ghcr.io/loong64 镜像) ----------
ensure_base_images() {
    for img in "${BASE_IMAGES[@]}"; do
        if docker image inspect "$img" &>/dev/null; then
            echo "    基础镜像就绪: $img"
            continue
        fi
        echo "    基础镜像缺失，尝试拉取: $img"
        if ! docker pull "$img"; then
            echo "[ERROR] 拉取失败: $img"
            exit 1
        fi
    done
}

# ---------- 构建前端 ----------
build_frontend() {
    echo "==> 构建前端静态文件..."
    cd "$PROJECT_DIR/frontend"
    if [ -d "vue-project/dist" ]; then
        echo "    前端构建产物已存在，跳过"
        return
    fi
    if [ ! -f "vue-project/package.json" ]; then
        echo "[ERROR] vue-project/package.json 不存在，无法构建前端"
        exit 1
    fi
    echo "    运行 bun build..."
    cd vue-project
    bun install --frozen-lockfile && bun run build
    cd "$PROJECT_DIR"
    echo "    前端构建完成"
}

# ---------- 构建单个镜像 ----------
build_image() {
    local img="$1"
    local context="${IMAGE_CONTEXT[$img]}"
    local dockerfile="${context}/Dockerfile"

    # 根据镜像名匹配对应的基础镜像
    local base_image
    if [[ "$img" =~ frontend ]]; then
        base_image="$BASE_IMAGE_FRONTEND"
    elif [[ "$img" =~ tool-server ]]; then
        base_image="$BASE_IMAGE_TOOL_SERVER"
    else
        base_image="$BASE_IMAGE_WEB_SERVER"
    fi

    echo "==> 构建镜像: $img (base: $base_image)"
    docker build \
        -t "$img" \
        -f "$dockerfile" \
        --build-arg BASE_IMAGE="$base_image" \
        --build-arg PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-}" \
        "$context"
    echo "    完成: $img"
}

# ---------- 主流程 ----------
cd "$PROJECT_DIR"

# 龙芯需预拉取基础镜像，x86 由 docker build 自动处理
if [ "$ARCH" = "loong64" ]; then
    echo "==> 检查基础镜像 (loong64)..."
    ensure_base_images
    echo ""
fi

# 确定要构建的镜像
if [ $# -gt 0 ]; then
    TARGETS=()
    for arg in "$@"; do
        found=false
        for img in "${ALL_IMAGES[@]}"; do
            if [[ "$img" =~ $arg ]]; then
                TARGETS+=("$img")
                found=true
            fi
        done
        if [ "$found" = false ]; then
            echo "[WARN] 未匹配任何镜像: $arg"
        fi
    done
else
    TARGETS=("${ALL_IMAGES[@]}")
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "[ERROR] 没有要构建的镜像"
    exit 1
fi

# 如果前端在构建列表中，先跑 npm build
for t in "${TARGETS[@]}"; do
    if [[ "$t" =~ frontend ]]; then
        build_frontend
        break
    fi
done

echo ""
for img in "${TARGETS[@]}"; do
    build_image "$img"
done

echo ""
echo "========== 构建完毕 =========="
for img in "${TARGETS[@]}"; do
    echo "  $img"
done
