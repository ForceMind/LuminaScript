#!/bin/bash

# ==========================================
# LuminaScript 一键部署脚本 (修复版 v2)
# ==========================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_DIR=$(pwd)
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
ENV_FILE="$BACKEND_DIR/.env"

echo -e "${BLUE}====== 妙笔流光 (LuminaScript) 部署助手 v3 ======${NC}"

# ================= 0. 环境预检 =================
echo -e "${YELLOW}[0/5] 环境预检...${NC}"

# 寻找合适的 Python 版本
target_py=""
# 优先查找高版本
for py_cmd in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    if command -v $py_cmd &>/dev/null; then
        VER_STR=$($py_cmd -c"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        # 检查是否 >= 3.8
        if (( $(echo "$VER_STR >= 3.8" | bc -l 2>/dev/null || awk -v v="$VER_STR" 'BEGIN{print(v>=3.8?1:0)}') )); then
            echo "发现可用 Python: $py_cmd ($VER_STR)"
            target_py=$py_cmd
            break
        fi
    fi
done

if [ -z "$target_py" ]; then
    echo -e "${RED}错误: 未找到 Python 3.8+。${NC}"
    echo "当前系统默认 Python 可能较旧 (如 3.6)。"
    echo "请安装较新版本 (例如: sudo yum install python39)，脚本会自动识别，不会影响系统默认设置。"
    exit 1
fi

# 检查 npm
HAS_NPM=0
if command -v npm &>/dev/null; then
    HAS_NPM=1
    echo "识别到 npm: $(npm -v)"
else
    echo -e "${YELLOW}警告: 未找到 npm。前端构建步骤将被跳过。${NC}"
    echo "请确保您已手动上传了编译好的 frontend/dist 目录。"
fi

# ================= 1. API 配置 =================
echo -e "${YELLOW}[1/5] 检查配置...${NC}"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}未检测到 .env，正在创建默认配置...${NC}"
    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-3.5-turbo
EOF
    echo -e "${GREEN}默认 .env 已创建。请务必编辑 $ENV_FILE 填入真实 Key。${NC}"
else
    echo "检测到现有配置。"
fi

# ================= 2. 代码更新 =================
echo -e "${YELLOW}[2/5] 拉取代码...${NC}"
if [ -d ".git" ]; then
    git pull || echo -e "${RED}Git 拉取失败，将使用现有代码继续...${NC}"
fi

# ================= 2.5 端口选择 (自动避让) =================
DEFAULT_PORT=8000
PORT=$DEFAULT_PORT
MAX_RETRIES=10

echo -e "${YELLOW}[2.5/5] 寻找可用端口...${NC}"

check_port() {
    local p=$1
    if command -v lsof &>/dev/null; then
        lsof -i:$p &>/dev/null
        return $?
    elif command -v netstat &>/dev/null; then
        netstat -nlp | grep ":$p " &>/dev/null
        return $?
    elif command -v fuser &>/dev/null; then
        fuser $p/tcp &>/dev/null
        return $?
    else
        # 如果没有工具，默认盲目尝试启动，或者假设端口可用
        return 1
    fi
}

for ((i=0; i<MAX_RETRIES; i++)); do
    if check_port $PORT; then
        echo -e "端口 $PORT 被占用，尝试下一端口..."
        PORT=$((PORT+1))
    else
        echo -e "${GREEN}将使用端口: $PORT${NC}"
        break
    fi
done

if ((i==MAX_RETRIES)); then
    echo -e "${RED}错误: 找不到可用端口 (尝试了 $DEFAULT_PORT - $PORT)。请清理服务器进程。${NC}"
    exit 1
fi

# ================= 3. 后端部署 =================
echo -e "${YELLOW}[3/5] 部署后端...${NC}"
cd "$BACKEND_DIR"

# 创建或激活 venv
if [ ! -d "$VENV_DIR" ]; then
    echo "使用 $target_py 创建虚拟环境..."
    $target_py -m venv venv
fi

source venv/bin/activate


# 尝试安装依赖
echo "安装/更新依赖..."
pip install --upgrade pip
# 尝试安装，若失败则提示
if ! pip install -r requirements.txt; then
    echo -e "${RED}依赖安装遇到问题！${NC}"
    echo "正在尝试放宽依赖版本限制..."
    # 尝试单独安装旧版 fastapi 兼容旧 python (虽然不推荐)
    pip install "fastapi<0.100.0" "pydantic<2.0.0" "typing_extensions" --no-deps
    pip install -r requirements.txt
fi

# ================= 4. 前端构建 =================
echo -e "${YELLOW}[4/5] 构建前端...${NC}"
if [ $HAS_NPM -eq 1 ]; then
    cd "$FRONTEND_DIR"
    if [ ! -d "node_modules" ]; then
        echo "安装前端依赖 (npm install)..."
        npm install
    fi
    echo "编译前端 (npm run build)..."
    npm run build
else
    echo "跳过前端构建 (无 npm)。"
fi

# ================= 5. 启动服务 =================
echo -e "${YELLOW}[5/5] 启动服务...${NC}"
cd "$BACKEND_DIR"
# 后台启动
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port $PORT > "$PROJECT_DIR/backend.log" 2>&1 &
SERVER_PID=$!

sleep 3
if ps -p $SERVER_PID > /dev/null; then
    echo -e "${GREEN}后端服务已启动! PID: $SERVER_PID (Port: $PORT)${NC}"
else
    echo -e "${RED}后端服务启动似乎失败了。请查看日志:${NC}"
    tail -n 10 "$PROJECT_DIR/backend.log"
    echo -e "${RED}提示: 如果是 ImportError，通常是 Python 版本过低导致。${NC}"
    exit 1
fi

# ================= 6. 完成信息 =================
# 获取 IP 地址
IP=$(hostname -I | awk '{print $1}')
PUBLIC_IP=$(curl -s --connect-timeout 2 ifconfig.me || echo "未知")

echo -e "\n${GREEN}====== 部署成功 ======${NC}"
echo -e "访问地址 (内网):  http://$IP:$PORT"
echo -e "访问地址 (公网):  http://$PUBLIC_IP:$PORT"
echo -e "前端构建目录:     $FRONTEND_DIR/dist"
echo -e "------------------------------------------------"
echo -e "注意: 若无法访问，请检查服务器防火墙 (安全组) 是否放行 $PORT 端口。"
echo -e "日志监控: tail -f $PROJECT_DIR/backend.log"
