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

remove_miaobi_link() {
    if [ -L "/usr/local/bin/miaobi" ]; then
        local target
        target="$(readlink -f /usr/local/bin/miaobi || true)"
        if [ "$target" = "$PROJECT_DIR/miaobi" ]; then
            rm -f /usr/local/bin/miaobi
            echo "已移除 /usr/local/bin/miaobi"
        fi
    elif [ -f "/usr/local/bin/miaobi" ]; then
        rm -f /usr/local/bin/miaobi
        echo "已移除 /usr/local/bin/miaobi"
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
backup_if_exists "$PROJECT_DIR/frontend.log"
backup_if_exists "$RUNTIME_FILE"

echo -e "${YELLOW}[2/4] 鍋滄鏈嶅姟...${NC}"
stop_services

echo -e "${YELLOW}[3/4] 娓呯悊鍛戒护鍏ュ彛涓庤繍琛屾枃浠?..${NC}"
remove_miaobi_link
rm -f "$RUNTIME_FILE"

if confirm "鏄惁鍒犻櫎 Python 铏氭嫙鐜 (backend/venv)"; then
    rm -rf "$BACKEND_DIR/venv"
    echo "宸插垹闄?backend/venv"
fi

if confirm "鏄惁鍒犻櫎鍓嶇鏋勫缓浜х墿涓庝緷璧?(frontend/dist, frontend/node_modules, frontend/server.cjs)"; then
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/node_modules"
    rm -f "$FRONTEND_DIR/server.cjs"
    echo "宸插垹闄ゅ墠绔瀯寤轰骇鐗╀笌渚濊禆"
fi

if confirm "鏄惁鍒犻櫎杩愯鏃ュ織 (backend.log, frontend.log)"; then
    rm -f "$PROJECT_DIR/backend.log" "$PROJECT_DIR/frontend.log"
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
