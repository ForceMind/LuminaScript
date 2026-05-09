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
RUNTIME_FILE="$PROJECT_DIR/.lumina_runtime"
GLOBAL_RUNTIME_FILE="/etc/miaobi/runtime.env"
BACKEND_PORT="8000"
FRONTEND_PORT="8600"
BACKEND_LOG="$PROJECT_DIR/backend.log"
FRONTEND_LOG="$PROJECT_DIR/frontend.log"

if [ -f "$RUNTIME_FILE" ]; then
    # shellcheck disable=SC1090
    source "$RUNTIME_FILE"
fi

BACKEND_DIR="${BACKEND_DIR:-$PROJECT_DIR/backend}"
FRONTEND_DIR="${FRONTEND_DIR:-$PROJECT_DIR/frontend}"
VENV_DIR="${VENV_DIR:-$BACKEND_DIR/venv}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-8600}"
BACKEND_LOG="${BACKEND_LOG:-$PROJECT_DIR/backend.log}"
FRONTEND_LOG="${FRONTEND_LOG:-$PROJECT_DIR/frontend.log}"
FRONTEND_SERVER_FILE="$FRONTEND_DIR/server.cjs"

mkdir -p "$BACKUP_DIR"

echo -e "${BLUE}====== 妙笔流光一键更新脚本 ======${NC}"
echo "项目目录: $PROJECT_DIR"

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
    echo "已备份: $source_path"
}

is_runtime_data_path() {
    local path="$1"
    case "$path" in
        backend/.env|.lumina_runtime|*.db|*.sqlite|*.sqlite3|*.log|frontend/dist/*|frontend/node_modules/*|backend/venv/*|backend/__pycache__/*|node_modules/*|backups/*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_auto_restore_path() {
    local path="$1"
    case "$path" in
        frontend/server.cjs|frontend/package-lock.json|miaobi|update.sh|uninstall.sh)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

install_miaobi() {
    if [ ! -f "$PROJECT_DIR/miaobi" ]; then
        return
    fi

    chmod 755 "$PROJECT_DIR/miaobi" || true
    ln -sfn "$PROJECT_DIR/miaobi" /usr/local/bin/miaobi
}

write_runtime_file() {
    cat > "$RUNTIME_FILE" <<EOF
PROJECT_DIR=$PROJECT_DIR
BACKEND_DIR=$BACKEND_DIR
FRONTEND_DIR=$FRONTEND_DIR
VENV_DIR=$VENV_DIR
BACKEND_PORT=$BACKEND_PORT
FRONTEND_PORT=$FRONTEND_PORT
BACKEND_LOG=$BACKEND_LOG
FRONTEND_LOG=$FRONTEND_LOG
EOF

    if mkdir -p "$(dirname "$GLOBAL_RUNTIME_FILE")" 2>/dev/null; then
        cat > "$GLOBAL_RUNTIME_FILE" <<EOF
PROJECT_DIR=$PROJECT_DIR
BACKEND_DIR=$BACKEND_DIR
FRONTEND_DIR=$FRONTEND_DIR
VENV_DIR=$VENV_DIR
BACKEND_PORT=$BACKEND_PORT
FRONTEND_PORT=$FRONTEND_PORT
BACKEND_LOG=$BACKEND_LOG
FRONTEND_LOG=$FRONTEND_LOG
EOF
        chmod 644 "$GLOBAL_RUNTIME_FILE" 2>/dev/null || true
    fi
}

restart_services_fallback() {
    echo -e "${YELLOW}未找到 miaobi，使用兼容模式重启服务。${NC}"

    # Prefer systemd if services exist
    if command -v systemctl >/dev/null 2>&1 && \
       [ -f "/etc/systemd/system/lumina-backend.service" ] && \
       [ -f "/etc/systemd/system/lumina-frontend.service" ]; then
        echo "检测到 systemd 服务，使用 systemctl 重启..."
        systemctl restart lumina-backend
        systemctl restart lumina-frontend
        sleep 3
        if systemctl is-active --quiet lumina-backend && systemctl is-active --quiet lumina-frontend; then
            echo -e "${GREEN}服务已通过 systemd 重启成功。${NC}"
        else
            echo -e "${RED}systemd 重启异常，请检查: journalctl -u lumina-backend -u lumina-frontend${NC}"
        fi
        return
    fi

    # Fallback to nohup (no systemd)
    if [ -x "$VENV_DIR/bin/uvicorn" ]; then
        local bpid=""
        pkill -f "$VENV_DIR/bin/uvicorn" 2>/dev/null || true
        bpid="$(lsof -t -i:$BACKEND_PORT 2>/dev/null || true)"
        if [ -n "$bpid" ]; then
            kill -9 $bpid 2>/dev/null || true
        fi
        cd "$BACKEND_DIR"
        nohup "$VENV_DIR/bin/uvicorn" main:app --app-dir "$BACKEND_DIR" --host 0.0.0.0 --port "$BACKEND_PORT" >> "$BACKEND_LOG" 2>&1 &
        cd "$PROJECT_DIR"
    fi

    if [ -f "$FRONTEND_SERVER_FILE" ]; then
        local fpid=""
        fpid="$(lsof -t -i:$FRONTEND_PORT 2>/dev/null || true)"
        if [ -n "$fpid" ]; then
            kill -9 "$fpid" 2>/dev/null || true
        fi
        nohup node "$FRONTEND_SERVER_FILE" >> "$FRONTEND_LOG" 2>&1 &
    fi
}

echo -e "${YELLOW}[1/6] 检查当前代码状态...${NC}"
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo -e "${RED}当前目录不是 Git 仓库，无法执行更新。${NC}"
    exit 1
fi

AUTO_RESTORE_PATHS=()
BLOCKING_CHANGES=()

while IFS= read -r line; do
    [ -z "$line" ] && continue

    path="${line:3}"
    if [[ "$path" == *" -> "* ]]; then
        path="${path##* -> }"
    fi

    if is_runtime_data_path "$path"; then
        continue
    fi

    if is_auto_restore_path "$path"; then
        AUTO_RESTORE_PATHS+=("$path")
        continue
    fi

    BLOCKING_CHANGES+=("$line")
done < <(git status --porcelain=v1 --untracked-files=all)

if [ "${#AUTO_RESTORE_PATHS[@]}" -gt 0 ]; then
    echo "检测到部署过程产生的本地文件变更，正在自动备份并恢复 Git 状态..."
    for path in "${AUTO_RESTORE_PATHS[@]}"; do
        backup_if_exists "$PROJECT_DIR/$path"
        git restore --source=HEAD --worktree --staged -- "$path"
        echo "已恢复: $path"
    done
fi

if [ "${#BLOCKING_CHANGES[@]}" -gt 0 ]; then
    echo -e "${RED}检测到代码或配置存在未提交修改。为避免覆盖人工改动，本次更新已停止。${NC}"
    printf '%s\n' "${BLOCKING_CHANGES[@]}"
    echo "请先确认这些修改是否需要保留。"
    exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ -z "$CURRENT_BRANCH" ] || [ "$CURRENT_BRANCH" = "HEAD" ]; then
    echo -e "${RED}当前不在有效分支上，无法安全拉取更新。${NC}"
    exit 1
fi

echo "当前分支: $CURRENT_BRANCH"

echo -e "${YELLOW}[2/6] 备份用户数据和配置...${NC}"
backup_if_exists "$BACKEND_DIR/.env"
backup_if_exists "$BACKEND_DIR/lumina.db"
backup_if_exists "$BACKEND_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/lumina.db"
backup_if_exists "$PROJECT_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/backend.log"
backup_if_exists "$PROJECT_DIR/frontend.log"
backup_if_exists "$RUNTIME_FILE"

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
if [ -f "$FRONTEND_DIR/package-lock.json" ]; then
    if ! npm ci; then
        echo -e "${YELLOW}npm ci 失败，回退到 npm install。${NC}"
        npm install
    fi
else
    npm install
fi

if ! npm run build; then
    echo -e "${YELLOW}检测到 npm run build 不可用，自动回退到 vite build。${NC}"
    npx vite build
fi

echo -e "${YELLOW}[6/6] 重启当前服务...${NC}"
cd "$PROJECT_DIR"

if [ -f "$PROJECT_DIR/miaobi" ]; then
    bash "$PROJECT_DIR/miaobi" restart
else
    restart_services_fallback
fi

write_runtime_file
install_miaobi

echo -e "${GREEN}更新完成。备份目录: $BACKUP_DIR${NC}"
echo "停止服务命令: miaobi stop"
