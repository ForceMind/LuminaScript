#!/bin/bash

# ==========================================
# LuminaScript 智能部署助手 (全栈版)
# ==========================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_DIR=$(pwd)
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
ENV_FILE="$BACKEND_DIR/.env"

# ================= 配置区 =================
# 在这里设置您期望的前端访问端口
FRONTEND_PORT=8600
# ========================================

echo -e "${BLUE}====== 妙笔流光 (LuminaScript) 部署助手 ======${NC}"

# ================= 0. 内存优化 (自动 SWAP) =================
# 解决低配服务器运行 dnf/yum/pip/npm 时的 "Killed" 问题
check_swap() {
    SWAP_SIZE=$(free -m | grep Swap | awk '{print $2}')
    if [ "$SWAP_SIZE" -eq 0 ]; then
        echo -e "${YELLOW}[0/6] 检测到无 Swap，正在创建 2GB 临时 Swap 以防 OOM...${NC}"
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q "/swapfile" /etc/fstab; then
            echo "/swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}Swap 创建成功!${NC}"
    else
        echo "检测到 Swap: ${SWAP_SIZE}MB (跳过创建)"
    fi
}
if [ "$EUID" -eq 0 ]; then
    check_swap
else
    echo -e "${YELLOW}非 root 用户运行，跳过 Swap 自动创建。${NC}"
fi

# ================= 1. 系统依赖安装 (含 Node.js) =================
echo -e "${YELLOW}[1/6] 检查并安装系统依赖 (Python & Node.js)...${NC}"

OS="Unknown"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
fi
echo "当前系统: $OS"

install_系统软件() {
    if [[ "$OS" == *"Alibaba"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
        # 安装 Node.js 18.x 源
        if ! command -v node > /dev/null; then
            echo "添加 Node.js 源..."
            curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash -
        fi
        
        sudo yum install -y epel-release 2>/dev/null
        # 尝试 dnf 或 yum
        PKG_MGR="yum"
        if command -v dnf > /dev/null; then PKG_MGR="dnf"; fi
        
        sudo $PKG_MGR install -y git nginx python3.11 python3.11-pip python3.11-devel bc nodejs lsof
    elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        # 安装 Node.js 18.x 源
        if ! command -v node > /dev/null; then
             curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
        fi
        sudo apt update -qq
        sudo apt install -y python3 python3-pip python3-venv git nginx bc nodejs lsof -qq
    fi
}

install_系统软件

# 验证 Node.js
if command -v node > /dev/null && command -v npm > /dev/null; then
    NODE_VER=$(node -v)
    echo -e "${GREEN}Node.js 已就绪: $NODE_VER${NC}"
else
    echo -e "${RED}Node.js 安装失败，前端无法构建。${NC}"
    exit 1
fi

# ================= 2. Python 环境配置 =================
echo -e "${YELLOW}[2/6] 配置 Python 环境...${NC}"

PYTHON_EXE=""
for callback in python3.12 python3.11 python3.10 python3; do
    if command -v $callback > /dev/null; then
        VER=$($callback -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        IS_OK=$(echo "$VER >= 3.10" | bc -l)
        if [ "$IS_OK" -eq 1 ]; then
            PYTHON_EXE=$callback
            echo "选定 Python: $PYTHON_EXE (版本 $VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo -e "${RED}[Error] 未找到 Python 3.10+。${NC}"
    exit 1
fi

# 重建 venv
if [ -d "$VENV_DIR" ]; then rm -rf "$VENV_DIR"; fi
echo "创建虚拟环境 ($VENV_DIR)..."
$PYTHON_EXE -m venv "$VENV_DIR"

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ================= 3. 配置文件 (.env) =================
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}[配置] 检测到首次运行 (或缺少 .env)，请配置 AI 服务信息:${NC}"
    read -p "请输入 LLM API Key (回车使用默认占位符): " INPUT_KEY
    
    # Default values suitable for the user's previous context (Xunfei/Spark)
    if [ -z "$INPUT_KEY" ]; then
        INPUT_KEY="your_key_here"
        echo "未输入 Key，将使用默认占位符。后续请手动编辑 backend/.env 修改。"
    fi

    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=$INPUT_KEY
LLM_BASE_URL=https://maas-api.cn-huabei-1.xf-yun.com/v2
LLM_MODEL_ID=xopglm47blth2
EOF
    echo -e "${GREEN}配置文件已生成: $ENV_FILE${NC}"
else
    echo "检测到现有配置文件 (.env)，跳过配置。"
fi

# ================= 3.1 管理员账户配置 =================
echo -e "${YELLOW}[3.1] 配置管理员账户${NC}"
read -p "是否修改默认管理员(admin)密码? [y/N] " MODIFY_ADMIN
ADMIN_USER_VAL="admin"
ADMIN_PASS_VAL="admin123"

if [[ "$MODIFY_ADMIN" =~ ^[Yy]$ ]]; then
    read -p "请输入管理员用户名 (默认 admin): " INPUT_USER
    if [ ! -z "$INPUT_USER" ]; then ADMIN_USER_VAL=$INPUT_USER; fi
    
    while true; do
        read -s -p "请输入管理员密码: " INPUT_PASS
        echo ""
        read -s -p "请再次输入密码: " INPUT_PASS2
        echo ""
        if [ "$INPUT_PASS" == "$INPUT_PASS2" ] && [ ! -z "$INPUT_PASS" ]; then
            ADMIN_PASS_VAL=$INPUT_PASS
            break
        else
            echo -e "${RED}密码不匹配或为空，请重试。${NC}"
        fi
    done
fi
echo -e "管理员将在部署时设置为: ${GREEN}$ADMIN_USER_VAL${NC}"

# ================= 4. 后端依赖 =================
echo -e "${YELLOW}[4/6] 安装后端依赖...${NC}"
if [ -d ".git" ]; then git pull; fi
echo "正在安装 Python 库..."
$VENV_PIP install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
$VENV_PIP install -r "$BACKEND_DIR/requirements.txt" -i https://mirrors.aliyun.com/pypi/simple/

# ================= 5. 前端构建 =================
echo -e "${YELLOW}[5/6] 构建前端资源...${NC}"
cd "$FRONTEND_DIR"

# 设置 npm 淘宝镜像加速
npm config set registry https://registry.npmmirror.com

echo "安装前端依赖..."
if [ ! -d "node_modules" ]; then
    npm install
else
    # 简单的全部重装耗时太久，尝试直接 install
    npm install
fi

echo "编译前端应用..."
# 尝试消除 vue-tsc 版本不兼容问题: 如果构建失败，尝试仅使用 vite build
if ! npm run build; then
    echo -e "${YELLOW}标准构建失败 (可能是 vue-tsc 类型检查问题)，尝试跳过类型检查强制构建...${NC}"
    # 临时使用 vite build - 为了纯净输出，我们这里不打印 tsc 信息
    # 直接运行 vite build (它应该在 PATH 中，如果不在则尝试 node_modules)
    if [ -f "./node_modules/.bin/vite" ]; then
        ./node_modules/.bin/vite build
    else
        npx vite build
    fi
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}构建再次失败!${NC}"
        echo "提示: 如果出现 'Killed' 错误，检查 Swap。"
        exit 1
    fi
fi

# ================= 6. 启动服务 =================
echo -e "${YELLOW}[6/6] 启动服务...${NC}"

# Python 端口检查
check_port() {
    $VENV_PYTHON -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); exit(0 if s.connect_ex(('127.0.0.1', $1)) != 0 else 1)"
    return $?
}

for ((i=0; i<3; i++)); do
    # 尝试kill以前可能残留的同名服务 (极其简单的防堆积逻辑)
    # 注意: 这里仅杀死关联到当前目录的 uvicorn
    pkill -f "$PROJECT_DIR/backend/venv/bin/uvicorn" 2>/dev/null
done

DEFAULT_PORT=8000
PORT=$DEFAULT_PORT

for ((i=0; i<10; i++)); do
    if check_port $PORT; then
        echo -e "${GREEN}使用端口: $PORT${NC}"
        break
    else
        echo "端口 $PORT 被占用，尝试 $((PORT+1))..."
        PORT=$((PORT+1))
    fi
done

cd "$BACKEND_DIR"

# 启用 Python 无缓冲模式，确保日志实时写入
export PYTHONUNBUFFERED=1

# 运行数据库升级与管理员设置
echo "应用数据库变更与管理员权限..."
ADMIN_USER="$ADMIN_USER_VAL" ADMIN_PASS="$ADMIN_PASS_VAL" "$VENV_PYTHON" upgrade_admin.py

# 生产环境建议去掉 --reload，增强稳定性
echo "启动后端服务 (Port: $PORT)..."
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port $PORT >> "$PROJECT_DIR/backend.log" 2>&1 &
PID=$!

sleep 5  # 增加等待时间，确保完全启动或报错退出
if ps -p $PID > /dev/null; then
    IP=$(hostname -I | awk '{print $1}')
    
    echo -e "${YELLOW}正在启动前端服务 (端口: $FRONTEND_PORT)...${NC}"
    
    # ----------------------------------------------------
    # 使用 Node.js + Express 搭建简易生产环境代理服务器
    # 解决 serve 无法代理 /api 请求导致的 404/undefined 问题
    # ----------------------------------------------------
    
    echo "安装生产环境服务依赖 (express, http-proxy-middleware)..."
    cd "$FRONTEND_DIR"
    npm install express http-proxy-middleware --no-save

    # 生成 server.cjs (使用 .cjs 避免 type: module 问题)
    cat > server.cjs <<EOF
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const app = express();

const BACKEND_PORT = $PORT;
const FRONTEND_PORT = $FRONTEND_PORT;
const API_URL = "http://127.0.0.1:" + BACKEND_PORT;

console.log("启动前端服务器...");
console.log("代理目标:", API_URL);

// 1. 配置 API 代理 (与 vite.config.ts 逻辑保持一致)
app.use('/api', createProxyMiddleware({ 
    target: API_URL, 
    changeOrigin: true,
    pathRewrite: { '^/api': '' },
    onProxyReq: (proxyReq, req, res) => {
        // 可选: 记录代理请求，方便调试
        // console.log('Proxy:', req.path, '->', API_URL + req.path);
    },
    onError: (err, req, res) => {
        console.error('Proxy Error:', err);
        res.status(500).send('Proxy Error');
    }
}));

// 2. 托管静态文件 (dist)
app.use(express.static(path.join(__dirname, 'dist')));

// 3. SPA 回退 (所有其他请求返回 index.html)
app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(FRONTEND_PORT, '0.0.0.0', () => {
  console.log(\`Frontend service running at http://0.0.0.0:\${FRONTEND_PORT}\`);
});
EOF

    # 清理旧的前端进程 (如果有)
    fpid=$(lsof -t -i:$FRONTEND_PORT)
    if [ -n "$fpid" ]; then
        kill -9 $fpid
    fi
    
    # 启动 Node 服务
    nohup node server.cjs > "$PROJECT_DIR/frontend.log" 2>&1 &
    
    echo -e "\n${GREEN}====== 部署成功 ======${NC}"
    echo -e "前端访问地址:  http://$IP:$FRONTEND_PORT"
    echo -e "后端 API 地址: http://$IP:$PORT"
    echo -e "--------------------------------------------------------"
    echo -e "前端日志:      tail -f $PROJECT_DIR/frontend.log"
    echo -e "后端日志:      tail -f $PROJECT_DIR/backend.log"
    echo -e "--------------------------------------------------------"
    echo -e "${YELLOW}重要提示: 请确保云服务器安全组/防火墙已放行端口: $PORT (后端) 和 $FRONTEND_PORT (前端)${NC}"
else
    echo -e "${RED}后端启动失败，请查看 backend.log${NC}"
    exit 1
fi
