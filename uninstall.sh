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
    echo -e "${YELLOW}停止现有服务...${NC}"
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
    fi
}

confirm() {
    local prompt="$1"
    local answer=""
    read -r -p "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

echo -e "${BLUE}====== 妙笔流光卸载脚本 ======${NC}"
echo "项目目录: $PROJECT_DIR"

echo -e "${YELLOW}[1/4] 备份关键数据...${NC}"
backup_if_exists "$BACKEND_DIR/.env"
backup_if_exists "$BACKEND_DIR/lumina_v2.db"
backup_if_exists "$BACKEND_DIR/lumina.db"
backup_if_exists "$PROJECT_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/lumina.db"
backup_if_exists "$PROJECT_DIR/backend.log"
backup_if_exists "$PROJECT_DIR/frontend.log"
backup_if_exists "$RUNTIME_FILE"

echo -e "${YELLOW}[2/4] 停止服务...${NC}"
stop_services

echo -e "${YELLOW}[3/4] 清理命令入口与运行文件...${NC}"
remove_miaobi_link
rm -f "$RUNTIME_FILE"

if confirm "是否删除 Python 虚拟环境 (backend/venv)"; then
    rm -rf "$BACKEND_DIR/venv"
    echo "已删除 backend/venv"
fi

if confirm "是否删除前端构建产物与依赖 (frontend/dist, frontend/node_modules, frontend/server.cjs)"; then
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/node_modules"
    rm -f "$FRONTEND_DIR/server.cjs"
    echo "已删除前端构建产物与依赖"
fi

if confirm "是否删除运行日志 (backend.log, frontend.log)"; then
    rm -f "$PROJECT_DIR/backend.log" "$PROJECT_DIR/frontend.log"
    echo "已删除运行日志"
fi

if confirm "是否删除数据库与配置文件 (.env, *.db)"; then
    rm -f "$BACKEND_DIR/.env" "$BACKEND_DIR/lumina_v2.db" "$BACKEND_DIR/lumina.db" "$PROJECT_DIR/lumina_v2.db" "$PROJECT_DIR/lumina.db"
    echo "已删除数据库与配置文件"
fi

echo -e "${YELLOW}[4/4] 卸载完成${NC}"
echo "备份目录: $BACKUP_DIR"
echo "如需彻底删除项目目录，请手动执行:"
echo "  rm -rf $PROJECT_DIR"
