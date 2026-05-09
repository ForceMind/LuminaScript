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
VENV_DIR="$BACKEND_DIR/venv"
ENV_FILE="$BACKEND_DIR/.env"
RUNTIME_FILE="$PROJECT_DIR/.lumina_runtime"
GLOBAL_RUNTIME_FILE="/etc/miaobi/runtime.env"
BACKEND_LOG="$PROJECT_DIR/backend.log"
FRONTEND_LOG="$PROJECT_DIR/frontend.log"
BACKEND_PORT_DEFAULT="8000"
FRONTEND_PORT_DEFAULT="8600"

BACKEND_PORT="$BACKEND_PORT_DEFAULT"
FRONTEND_PORT="$FRONTEND_PORT_DEFAULT"

if [ -f "$RUNTIME_FILE" ]; then
    # shellcheck disable=SC1090
    source "$RUNTIME_FILE"
fi

BACKEND_DIR="${BACKEND_DIR:-$PROJECT_DIR/backend}"
FRONTEND_DIR="${FRONTEND_DIR:-$PROJECT_DIR/frontend}"
VENV_DIR="${VENV_DIR:-$BACKEND_DIR/venv}"
BACKEND_PORT="${BACKEND_PORT:-$BACKEND_PORT_DEFAULT}"
FRONTEND_PORT="${FRONTEND_PORT:-$FRONTEND_PORT_DEFAULT}"
BACKEND_LOG="${BACKEND_LOG:-$PROJECT_DIR/backend.log}"
FRONTEND_LOG="${FRONTEND_LOG:-$PROJECT_DIR/frontend.log}"

PKG_MGR=""
OS_NAME="Unknown"
OS_ID=""
OS_LIKE=""
PYTHON_BIN=""
UPDATE_ADMIN="false"
ADMIN_USER="admin"
ADMIN_PASS="admin123"

print_header() {
    echo -e "${BLUE}====== 妙笔流光 (LuminaScript) 部署助手 ======${NC}"
    echo "项目目录: $PROJECT_DIR"
}

require_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}请使用 root 或 sudo 执行部署脚本。${NC}"
        exit 1
    fi
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

ensure_utf8_output() {
    local charmap
    charmap="$(locale charmap 2>/dev/null || true)"

    case "${charmap:-}" in
        UTF-8|utf8|UTF8)
            return
            ;;
    esac

    if command_exists locale; then
        local candidate
        for candidate in C.UTF-8 en_US.utf8 en_US.UTF-8 zh_CN.utf8 zh_CN.UTF-8; do
            if locale -a 2>/dev/null | grep -iqx "$candidate"; then
                export LANG="$candidate"
                export LC_ALL="$candidate"
                return
            fi
        done
    fi

    echo "[WARN] Non-UTF-8 locale detected; Chinese output may be garbled."
    echo "[WARN] Recommend: export LANG=C.UTF-8 LC_ALL=C.UTF-8"
}

detect_os_and_pkg_manager() {
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        OS_NAME="${NAME:-Unknown}"
        OS_ID="$(echo "${ID:-}" | tr '[:upper:]' '[:lower:]')"
        OS_LIKE="$(echo "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]')"
    fi

    if command_exists dnf; then
        PKG_MGR="dnf"
    elif command_exists yum; then
        PKG_MGR="yum"
    elif command_exists apt-get; then
        PKG_MGR="apt-get"
    else
        echo -e "${RED}未找到可用包管理器（dnf/yum/apt-get）。${NC}"
        exit 1
    fi

    echo "当前系统: $OS_NAME"
    echo "ID=$OS_ID, ID_LIKE=$OS_LIKE"
    echo "包管理器: $PKG_MGR"
}

pkg_update_cache() {
    if [ "$PKG_MGR" = "apt-get" ]; then
        apt-get update -y
    else
        "$PKG_MGR" makecache -y || true
    fi
}

pkg_install_required() {
    if [ "$PKG_MGR" = "apt-get" ]; then
        apt-get install -y "$@"
    else
        "$PKG_MGR" install -y "$@"
    fi
}

pkg_install_optional() {
    if [ "$PKG_MGR" = "apt-get" ]; then
        apt-get install -y "$@" || true
    else
        "$PKG_MGR" install -y "$@" || true
    fi
}

check_swap() {
    local swap_size
    swap_size="$(free -m | awk '/Swap:/ {print $2}')"
    swap_size="${swap_size:-0}"

    if [ "$swap_size" -gt 0 ]; then
        echo "检测到 Swap: ${swap_size}MB (跳过创建)"
        return
    fi

    echo -e "${YELLOW}[0/6] 未检测到 Swap，正在创建 2GB 交换分区...${NC}"
    if command_exists fallocate; then
        fallocate -l 2G /swapfile
    else
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
    fi
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo -e "${GREEN}Swap 创建完成。${NC}"
}

node_major() {
    if ! command_exists node; then
        echo 0
        return
    fi
    node -v | sed -E 's/^v([0-9]+).*/\1/'
}

ensure_node_from_binary() {
    local node_version arch arch_alias tmpdir tarball_url extract_dir install_dir
    node_version="${1:-18.20.8}"
    arch="$(uname -m)"

    case "$arch" in
        x86_64|amd64) arch_alias="x64" ;;
        aarch64|arm64) arch_alias="arm64" ;;
        *)
            echo -e "${RED}不支持的 CPU 架构: $arch，无法自动安装 Node.js 二进制包。${NC}"
            return 1
            ;;
    esac

    tmpdir="$(mktemp -d)"
    tarball_url="https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-${arch_alias}.tar.xz"
    echo "从官方源安装 Node.js v${node_version}..."
    curl -fsSL "$tarball_url" -o "${tmpdir}/node.tar.xz"
    tar -xJf "${tmpdir}/node.tar.xz" -C "$tmpdir"

    extract_dir="${tmpdir}/node-v${node_version}-linux-${arch_alias}"
    install_dir="/usr/local/node-v${node_version}-linux-${arch_alias}"
    rm -rf "$install_dir"
    mv "$extract_dir" "$install_dir"

    ln -sf "${install_dir}/bin/node" /usr/local/bin/node
    ln -sf "${install_dir}/bin/npm" /usr/local/bin/npm
    ln -sf "${install_dir}/bin/npx" /usr/local/bin/npx
    [ -f "${install_dir}/bin/corepack" ] && ln -sf "${install_dir}/bin/corepack" /usr/local/bin/corepack || true

    rm -rf "$tmpdir"
}

ensure_node() {
    local major
    major="$(node_major)"
    if [ "$major" -ge 18 ] && command_exists npm; then
        echo "Node.js 已可用: $(node -v)"
        return
    fi

    echo "尝试通过系统包安装 Node.js..."
    if [ "$PKG_MGR" = "apt-get" ]; then
        pkg_install_optional nodejs npm
    else
        pkg_install_optional nodejs npm
        if ! command_exists node && command_exists nodejs; then
            ln -sf "$(command -v nodejs)" /usr/local/bin/node || true
        fi
    fi

    major="$(node_major)"
    if [ "$major" -lt 18 ] || ! command_exists npm; then
        echo "系统源 Node.js 版本不足或 npm 缺失，回退到官方二进制安装..."
        ensure_node_from_binary "18.20.8"
    fi

    major="$(node_major)"
    if [ "$major" -lt 18 ] || ! command_exists npm; then
        echo -e "${RED}Node.js 安装失败，要求 Node.js >= 18。${NC}"
        exit 1
    fi

    echo -e "${GREEN}Node.js 就绪: $(node -v)${NC}"
}

choose_python() {
    local candidate ok
    for candidate in python3.12 python3.11 python3.10 python3; do
        if ! command_exists "$candidate"; then
            continue
        fi
        ok="$("$candidate" - <<'PY'
import sys
print(1 if sys.version_info >= (3, 10) else 0)
PY
)"
        if [ "$ok" = "1" ]; then
            PYTHON_BIN="$candidate"
            echo "使用 Python: $PYTHON_BIN ($("$candidate" -V 2>&1))"
            return
        fi
    done

    echo -e "${RED}未找到 Python 3.10+。${NC}"
    exit 1
}

setup_backend_env_and_deps() {
    echo -e "${YELLOW}[2/6] 创建 Python 虚拟环境并安装依赖...${NC}"

    choose_python

    if [ ! -d "$VENV_DIR" ]; then
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi

    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt"
}

configure_env_file() {
    echo -e "${YELLOW}[3/6] 检查后端配置 (.env)...${NC}"
    local keep_existing ans api_key base_url model_id

    keep_existing="y"
    if [ -f "$ENV_FILE" ]; then
        read -r -p "检测到已存在 .env，是否保留当前配置? [Y/n] " ans
        if [[ "${ans:-Y}" =~ ^[Nn]$ ]]; then
            keep_existing="n"
        fi
    else
        keep_existing="n"
    fi

    if [ "$keep_existing" = "y" ]; then
        echo "保留现有 .env。"
        return
    fi

    read -r -p "请输入 LLM API Key: " api_key
    read -r -p "请输入 LLM Base URL [默认: https://maas-api.cn-huabei-1.xf-yun.com/v1]: " base_url
    read -r -p "请输入 LLM 模型 ID [默认: xopglm47blth2]: " model_id

    api_key="${api_key:-your_key_here}"
    base_url="${base_url:-https://maas-api.cn-huabei-1.xf-yun.com/v1}"
    model_id="${model_id:-xopglm47blth2}"

    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=$api_key
LLM_BASE_URL=$base_url
LLM_MODEL_ID=$model_id
EOF

    echo -e "${GREEN}.env 已写入。${NC}"
}

configure_admin_policy() {
    echo -e "${YELLOW}[3.1/6] 管理员账户策略...${NC}"
    echo "1) 保持现有管理员（推荐）"
    echo "2) 重置为默认管理员 admin / admin123"
    echo "3) 设置新的管理员账号和密码"

    local choice u p p2
    read -r -p "请选择 [1/2/3，默认1]: " choice
    choice="${choice:-1}"

    case "$choice" in
        1)
            UPDATE_ADMIN="false"
            ;;
        2)
            UPDATE_ADMIN="true"
            ADMIN_USER="admin"
            ADMIN_PASS="admin123"
            ;;
        3)
            UPDATE_ADMIN="true"
            read -r -p "管理员用户名 [默认 admin]: " u
            ADMIN_USER="${u:-admin}"
            while true; do
                read -r -s -p "管理员密码: " p
                echo
                read -r -s -p "确认密码: " p2
                echo
                if [ -n "$p" ] && [ "$p" = "$p2" ]; then
                    ADMIN_PASS="$p"
                    break
                fi
                echo -e "${RED}两次密码不一致，请重新输入。${NC}"
            done
            ;;
        *)
            echo "未识别选项，保持现有管理员。"
            UPDATE_ADMIN="false"
            ;;
    esac
}

build_frontend() {
    echo -e "${YELLOW}[5/6] 安装前端依赖并构建...${NC}"
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
        echo -e "${YELLOW}npm run build 失败，回退到 npx vite build。${NC}"
        npx vite build
    fi
}

pick_backend_port() {
    local port
    port="$BACKEND_PORT_DEFAULT"
    while lsof -t -i:"$port" >/dev/null 2>&1; do
        port=$((port + 1))
    done
    BACKEND_PORT="$port"
}

write_frontend_server() {
    cat > "$FRONTEND_DIR/server.cjs" <<EOF
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const fs = require('fs');

const app = express();

function parseRuntimeFile(filePath) {
  try {
    if (!fs.existsSync(filePath)) return {};
    const raw = fs.readFileSync(filePath, 'utf8');
    const result = {};
    for (const lineRaw of raw.split(/\\r?\\n/)) {
      const line = String(lineRaw || '').trim();
      if (!line || line.startsWith('#')) continue;
      const idx = line.indexOf('=');
      if (idx <= 0) continue;
      const key = line.slice(0, idx).trim();
      const value = line.slice(idx + 1).trim().replace(/^['"]|['"]$/g, '');
      if (key) result[key] = value;
    }
    return result;
  } catch (err) {
    return {};
  }
}

const runtimePath = path.resolve(__dirname, '..', '.lumina_runtime');
const runtime = parseRuntimeFile(runtimePath);
const BACKEND_PORT = Number(process.env.BACKEND_PORT || runtime.BACKEND_PORT || ${BACKEND_PORT});
const FRONTEND_PORT = Number(process.env.FRONTEND_PORT || runtime.FRONTEND_PORT || ${FRONTEND_PORT});

app.use('/api', createProxyMiddleware({
  target: \`http://127.0.0.1:\${BACKEND_PORT}\`,
  changeOrigin: true,
  pathRewrite: { '^/api': '' },
  xfwd: true,
  proxyTimeout: 600000,
  timeout: 600000
}));

app.use(express.static(path.join(__dirname, 'dist')));
app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(FRONTEND_PORT, '0.0.0.0', () => {
  console.log(\`Frontend service running at http://0.0.0.0:\${FRONTEND_PORT}\`);
});
EOF
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

SYSTEMD_BACKEND_SERVICE="lumina-backend"
SYSTEMD_FRONTEND_SERVICE="lumina-frontend"
SYSTEMD_BACKEND_FILE="/etc/systemd/system/${SYSTEMD_BACKEND_SERVICE}.service"
SYSTEMD_FRONTEND_FILE="/etc/systemd/system/${SYSTEMD_FRONTEND_SERVICE}.service"

start_services() {
    echo -e "${YELLOW}[6/6] 启动后端与前端服务...${NC}"

    UPDATE_ADMIN="$UPDATE_ADMIN" ADMIN_USER="$ADMIN_USER" ADMIN_PASS="$ADMIN_PASS" \
        "$VENV_DIR/bin/python" "$BACKEND_DIR/upgrade_admin.py"

    pkill -f "$VENV_DIR/bin/uvicorn" 2>/dev/null || true
    pick_backend_port
    write_runtime_file
    write_frontend_server

    if command_exists systemctl; then
        setup_systemd_services
    else
        start_services_nohup
    fi
}

setup_systemd_services() {
    # Stop old nohup processes if any
    pkill -f "$VENV_DIR/bin/uvicorn" 2>/dev/null || true
    local fpid
    fpid="$(lsof -t -i:"$FRONTEND_PORT" 2>/dev/null || true)"
    [ -n "$fpid" ] && kill -9 $fpid 2>/dev/null || true
    sleep 1

    local node_bin
    node_bin="$(command -v node)"

    cat > "$SYSTEMD_BACKEND_FILE" <<SVCEOF
[Unit]
Description=LuminaScript Backend (uvicorn)
After=network.target

[Service]
Type=simple
WorkingDirectory=$BACKEND_DIR
ExecStart=$VENV_DIR/bin/uvicorn main:app --app-dir $BACKEND_DIR --host 0.0.0.0 --port $BACKEND_PORT
Restart=always
RestartSec=3
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=append:$BACKEND_LOG
StandardError=append:$BACKEND_LOG

[Install]
WantedBy=multi-user.target
SVCEOF

    cat > "$SYSTEMD_FRONTEND_FILE" <<SVCEOF
[Unit]
Description=LuminaScript Frontend (Node.js)
After=network.target

[Service]
Type=simple
WorkingDirectory=$FRONTEND_DIR
ExecStart=$node_bin $FRONTEND_DIR/server.cjs
Restart=always
RestartSec=3
Environment=NODE_ENV=production
StandardOutput=append:$FRONTEND_LOG
StandardError=append:$FRONTEND_LOG

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload
    systemctl enable "$SYSTEMD_BACKEND_SERVICE"
    systemctl restart "$SYSTEMD_BACKEND_SERVICE"

    sleep 3
    if ! systemctl is-active --quiet "$SYSTEMD_BACKEND_SERVICE"; then
        echo -e "${RED}后端 systemd 服务启动失败，请查看: journalctl -u $SYSTEMD_BACKEND_SERVICE${NC}"
        exit 1
    fi
    echo -e "${GREEN}后端服务已通过 systemd 启动 (端口 $BACKEND_PORT)${NC}"

    systemctl enable "$SYSTEMD_FRONTEND_SERVICE"
    systemctl restart "$SYSTEMD_FRONTEND_SERVICE"

    sleep 2
    if ! systemctl is-active --quiet "$SYSTEMD_FRONTEND_SERVICE"; then
        echo -e "${RED}前端 systemd 服务启动失败，请查看: journalctl -u $SYSTEMD_FRONTEND_SERVICE${NC}"
        exit 1
    fi
    echo -e "${GREEN}前端服务已通过 systemd 启动 (端口 $FRONTEND_PORT)${NC}"
}

start_services_nohup() {
    nohup "$VENV_DIR/bin/uvicorn" main:app --app-dir "$BACKEND_DIR" --host 0.0.0.0 --port "$BACKEND_PORT" >> "$BACKEND_LOG" 2>&1 &

    sleep 3
    if ! lsof -t -i:"$BACKEND_PORT" >/dev/null 2>&1; then
        echo -e "${RED}后端启动失败，请查看日志: $BACKEND_LOG${NC}"
        exit 1
    fi

    local fpid
    fpid="$(lsof -t -i:"$FRONTEND_PORT" 2>/dev/null || true)"
    [ -n "$fpid" ] && kill -9 $fpid 2>/dev/null || true
    nohup node "$FRONTEND_DIR/server.cjs" >> "$FRONTEND_LOG" 2>&1 &

    sleep 2
    if ! lsof -t -i:"$FRONTEND_PORT" >/dev/null 2>&1; then
        echo -e "${RED}前端启动失败，请查看日志: $FRONTEND_LOG${NC}"
        exit 1
    fi
}

install_system_dependencies() {
    echo -e "${YELLOW}[1/6] 安装系统依赖 (Python / Node.js / Nginx)...${NC}"
    detect_os_and_pkg_manager
    pkg_update_cache

    if [ "$PKG_MGR" = "apt-get" ]; then
        pkg_install_required git nginx curl ca-certificates xz-utils tar python3 python3-pip python3-venv bc lsof
    else
        pkg_install_required git nginx curl ca-certificates xz tar python3 python3-pip bc lsof
        pkg_install_optional python3-devel
        pkg_install_optional python3.11-devel
    fi

    ensure_node
}

print_finish() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    ip="${ip:-127.0.0.1}"

    echo -e "\n${GREEN}====== 部署完成 ======${NC}"
    echo "前端访问地址: http://$ip:$FRONTEND_PORT"
    echo "后端 API 地址: http://$ip:$BACKEND_PORT"
    echo "后端日志: $BACKEND_LOG"
    echo "前端日志: $FRONTEND_LOG"
    echo "运维命令: miaobi"
}

main() {
    ensure_utf8_output
    print_header
    require_root
    check_swap
    install_system_dependencies
    setup_backend_env_and_deps
    configure_env_file
    configure_admin_policy
    build_frontend
    start_services
    install_miaobi
    print_finish
}

main "$@"
