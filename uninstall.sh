#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
RUNTIME_FILE="$PROJECT_DIR/.lumina_runtime"
GLOBAL_RUNTIME_FILE="/etc/miaobi/runtime.env"
BACKUP_DIR="$PROJECT_DIR/backups/uninstall_$(date +%Y%m%d_%H%M%S)"
BACKEND_PORT="8000"
FRONTEND_PORT="8600"

if [ -f "$RUNTIME_FILE" ]; then
    # shellcheck disable=SC1090
    source "$RUNTIME_FILE"
fi

mkdir -p "$BACKUP_DIR"

backup_if_exists() {
    local source_path="$1"
    local relative_path
    local target_dir

    if [ ! -e "$source_path" ]; then
        return
    fi

    relative_path="${source_path#$PROJECT_DIR/}"
    if [ "$relative_path" = "$source_path" ]; then
        relative_path="$(basename "$source_path")"
    fi

    target_dir="$BACKUP_DIR/$(dirname "$relative_path")"
    mkdir -p "$target_dir"
    cp -a "$source_path" "$target_dir/"
}

stop_services() {
    echo -e "${YELLOW}鍋滄鐜版湁鏈嶅姟...${NC}"
    bash "$PROJECT_DIR/miaobi" stop || true
}

remove_systemd_services() {
    if ! command -v systemctl >/dev/null 2>&1; then
        return
    fi

    local service_name
    for service_name in lumina-backend lumina-worker lumina-frontend; do
        systemctl disable --now "$service_name" 2>/dev/null || true
        rm -f "/etc/systemd/system/${service_name}.service"
        rm -rf "/etc/systemd/system/${service_name}.service.d"
    done
    systemctl daemon-reload
    systemctl reset-failed 2>/dev/null || true
}

remove_miaobi_link() {
    if [ -L "/usr/local/bin/miaobi" ]; then
        local target
        target="$(readlink -f /usr/local/bin/miaobi || true)"
        if [ "$target" = "$PROJECT_DIR/miaobi" ]; then
            rm -f /usr/local/bin/miaobi
            echo "已移除 /usr/local/bin/miaobi"
        fi
    elif [ -f "/usr/local/bin/miaobi" ]; then
        if cmp -s "$PROJECT_DIR/miaobi" "/usr/local/bin/miaobi"; then
            rm -f /usr/local/bin/miaobi
            echo "已移除 /usr/local/bin/miaobi"
        else
            echo -e "${YELLOW}保留不属于当前项目的 /usr/local/bin/miaobi${NC}"
        fi
    fi
}

remove_global_runtime_file() {
    local runtime_project=""
    if [ ! -f "$GLOBAL_RUNTIME_FILE" ]; then
        return
    fi
    runtime_project="$(sed -n 's/^PROJECT_DIR=//p' "$GLOBAL_RUNTIME_FILE" | head -n 1)"
    runtime_project="${runtime_project%\"}"
    runtime_project="${runtime_project#\"}"
    runtime_project="${runtime_project%\'}"
    runtime_project="${runtime_project#\'}"
    if [ "$runtime_project" = "$PROJECT_DIR" ]; then
        rm -f "$GLOBAL_RUNTIME_FILE"
        echo "已移除全局运行时配置: $GLOBAL_RUNTIME_FILE"
    fi
}

confirm() {
    local prompt="$1"
    local answer=""
    read -r -p "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

echo -e "${BLUE}====== 濡欑瑪娴佸厜鍗歌浇鑴氭湰 ======${NC}"
echo "椤圭洰鐩綍: $PROJECT_DIR"

echo -e "${YELLOW}[1/4] 澶囦唤鍏抽敭鏁版嵁...${NC}"
backup_if_exists "$BACKEND_DIR/.env"
backup_if_exists "$BACKEND_DIR/lumina_v2.db"
backup_if_exists "$BACKEND_DIR/lumina.db"
backup_if_exists "$PROJECT_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/lumina.db"
backup_if_exists "$PROJECT_DIR/backend.log"
backup_if_exists "$PROJECT_DIR/worker.log"
backup_if_exists "$PROJECT_DIR/frontend.log"
backup_if_exists "$RUNTIME_FILE"

echo -e "${YELLOW}[2/4] 鍋滄鏈嶅姟...${NC}"
stop_services

echo -e "${YELLOW}[3/4] 娓呯悊鍛戒护鍏ュ彛涓庤繍琛屾枃浠?..${NC}"
remove_systemd_services
remove_miaobi_link
remove_global_runtime_file
rm -f "$RUNTIME_FILE"

if confirm "鏄惁鍒犻櫎 Python 铏氭嫙鐜 (backend/venv)"; then
    rm -rf "$BACKEND_DIR/venv"
    echo "宸插垹闄?backend/venv"
fi

if confirm "鏄惁鍒犻櫎鍓嶇鏋勫缓浜х墿涓庝緷璧?(frontend/dist, frontend/node_modules)"; then
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/node_modules"
    echo "宸插垹闄ゅ墠绔瀯寤轰骇鐗╀笌渚濊禆"
fi

if confirm "鏄惁鍒犻櫎杩愯鏃ュ織 (backend.log, worker.log, frontend.log)"; then
    rm -f "$PROJECT_DIR/backend.log" "$PROJECT_DIR/worker.log" "$PROJECT_DIR/frontend.log"
    echo "宸插垹闄よ繍琛屾棩蹇?
fi

if confirm "鏄惁鍒犻櫎鏁版嵁搴撲笌閰嶇疆鏂囦欢 (.env, *.db)"; then
    rm -f "$BACKEND_DIR/.env" "$BACKEND_DIR/lumina_v2.db" "$BACKEND_DIR/lumina.db" "$PROJECT_DIR/lumina_v2.db" "$PROJECT_DIR/lumina.db"
    echo "宸插垹闄ゆ暟鎹簱涓庨厤缃枃浠?
fi

echo -e "${YELLOW}[4/4] 鍗歌浇瀹屾垚${NC}"
echo "澶囦唤鐩綍: $BACKUP_DIR"
echo "濡傞渶褰诲簳鍒犻櫎椤圭洰鐩綍锛岃鎵嬪姩鎵ц:"
echo "  rm -rf $PROJECT_DIR"
