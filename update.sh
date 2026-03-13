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
BACKUP_DIR="$PROJECT_DIR/backups/update_$(date +%Y%m%d_%H%M%S)"
VENV_DIR="$BACKEND_DIR/venv"

echo -e "${BLUE}====== 妙笔流光一键更新脚本 ======${NC}"
echo "项目目录: $PROJECT_DIR"

if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo -e "${RED}当前目录不是 Git 仓库，无法执行更新。${NC}"
    exit 1
fi

echo -e "${YELLOW}[1/6] 检查当前代码状态...${NC}"
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}检测到未提交的本地修改。为避免覆盖你的改动，本次更新已停止。${NC}"
    echo "请先提交、暂存或清理工作区后再执行。"
    exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ -z "$CURRENT_BRANCH" ] || [ "$CURRENT_BRANCH" = "HEAD" ]; then
    echo -e "${RED}当前不在有效分支上，无法安全拉取更新。${NC}"
    exit 1
fi
echo "当前分支: $CURRENT_BRANCH"

echo -e "${YELLOW}[2/6] 备份用户数据和配置...${NC}"
mkdir -p "$BACKUP_DIR"

backup_if_exists() {
    local source_path="$1"
    if [ -e "$source_path" ]; then
        cp -a "$source_path" "$BACKUP_DIR/"
        echo "已备份: $source_path"
    fi
}

backup_if_exists "$BACKEND_DIR/.env"
backup_if_exists "$BACKEND_DIR/lumina.db"
backup_if_exists "$BACKEND_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/backend.log"
backup_if_exists "$PROJECT_DIR/frontend.log"

echo -e "${YELLOW}[3/6] 拉取最新代码...${NC}"
git fetch origin
git pull --ff-only origin "$CURRENT_BRANCH"

echo -e "${YELLOW}[4/6] 更新后端依赖...${NC}"
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}未找到可用的 Python 3。${NC}"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "未找到虚拟环境，正在创建..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PIP" install -r "$BACKEND_DIR/requirements.txt"

echo -e "${YELLOW}[5/6] 构建前端...${NC}"
cd "$FRONTEND_DIR"
npm install
if ! npm run build; then
    echo -e "${YELLOW}检测到 npm run build 不可用，自动回退到 vite build。${NC}"
    npx vite build
fi

echo -e "${YELLOW}[6/6] 重启当前服务...${NC}"
cd "$PROJECT_DIR"

if [ -x "$VENV_DIR/bin/uvicorn" ]; then
    pkill -f "$VENV_DIR/bin/uvicorn" 2>/dev/null || true
    nohup "$VENV_DIR/bin/uvicorn" main:app --app-dir "$BACKEND_DIR" --host 0.0.0.0 --port 8000 >> "$PROJECT_DIR/backend.log" 2>&1 &
    echo "后端已重启: 8000"
else
    echo -e "${YELLOW}未找到 uvicorn，已跳过后端重启。${NC}"
fi

if [ -f "$PROJECT_DIR/server.cjs" ]; then
    fpid="$(lsof -t -i:8600 2>/dev/null || true)"
    if [ -n "${fpid:-}" ]; then
        kill -9 "$fpid" 2>/dev/null || true
    fi
    nohup node "$PROJECT_DIR/server.cjs" >> "$PROJECT_DIR/frontend.log" 2>&1 &
    echo "前端已重启: 8600"
else
    echo -e "${YELLOW}未找到 server.cjs，已完成代码更新和前端构建。${NC}"
fi

echo -e "${GREEN}更新完成。备份目录: $BACKUP_DIR${NC}"
