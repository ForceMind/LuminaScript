#!/bin/bash

# ==========================================
# LuminaScript 鏅鸿兘閮ㄧ讲鍔╂墜 (鍏ㄦ爤鐗?
# ==========================================

# 棰滆壊瀹氫箟
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
RUNTIME_FILE="$PROJECT_DIR/.lumina_runtime"

# ================= 閰嶇疆鍖?=================
# 鍦ㄨ繖閲岃缃偍鏈熸湜鐨勫墠绔闂鍙?
FRONTEND_PORT=8600
# ========================================

echo -e "${BLUE}====== 濡欑瑪娴佸厜 (LuminaScript) 閮ㄧ讲鍔╂墜 ======${NC}"

# ================= 0. 鍐呭瓨浼樺寲 (鑷姩 SWAP) =================
# 瑙ｅ喅浣庨厤鏈嶅姟鍣ㄨ繍琛?dnf/yum/pip/npm 鏃剁殑 "Killed" 闂
check_swap() {
    SWAP_SIZE=$(free -m | grep Swap | awk '{print $2}')
    if [ "$SWAP_SIZE" -eq 0 ]; then
        echo -e "${YELLOW}[0/6] 妫€娴嬪埌鏃?Swap锛屾鍦ㄥ垱寤?2GB 涓存椂 Swap 浠ラ槻 OOM...${NC}"
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q "/swapfile" /etc/fstab; then
            echo "/swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}Swap 鍒涘缓鎴愬姛!${NC}"
    else
        echo "妫€娴嬪埌 Swap: ${SWAP_SIZE}MB (璺宠繃鍒涘缓)"
    fi
}
if [ "$EUID" -eq 0 ]; then
    check_swap
else
    echo -e "${YELLOW}闈?root 鐢ㄦ埛杩愯锛岃烦杩?Swap 鑷姩鍒涘缓銆?{NC}"
fi

# ================= 1. 绯荤粺渚濊禆瀹夎 (鍚?Node.js) =================
echo -e "${YELLOW}[1/6] 妫€鏌ュ苟瀹夎绯荤粺渚濊禆 (Python & Node.js)...${NC}"

OS="Unknown"
OS_ID=""
OS_LIKE=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS="${NAME:-Unknown}"
    OS_ID=$(echo "${ID:-}" | tr '[:upper:]' '[:lower:]')
    OS_LIKE=$(echo "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]')
fi
echo "褰撳墠绯荤粺: $OS"
echo "ID=$OS_ID, ID_LIKE=$OS_LIKE"

get_node_major() {
    if ! command -v node > /dev/null; then
        echo 0
        return
    fi
    node -v | sed -E 's/^v([0-9]+).*/\1/'
}

install_system_packages() {
    if [[ "$OS_ID" =~ (opencloudos|alinux|centos|rhel|rocky|almalinux|anolis|ol|fedora) ]] || [[ "$OS_LIKE" == *"rhel"* ]] || [[ "$OS_LIKE" == *"fedora"* ]]; then
        PKG_MGR="yum"
        if command -v dnf > /dev/null; then PKG_MGR="dnf"; fi

        NODE_MAJOR=$(get_node_major)
        if [ "$NODE_MAJOR" -lt 18 ] || ! command -v npm > /dev/null; then
            echo "閰嶇疆 NodeSource Node.js 18.x 浠撳簱..."
            if ! curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo -E bash -; then
                echo "NodeSource script unsupported here, fallback to distro repo."
            fi
        fi

        sudo $PKG_MGR makecache -y 2>/dev/null || true
        sudo $PKG_MGR install -y git nginx python3 python3-pip bc lsof
        sudo $PKG_MGR install -y python3-devel 2>/dev/null || sudo $PKG_MGR install -y python3.11-devel 2>/dev/null || true
        sudo $PKG_MGR install -y nodejs npm 2>/dev/null || sudo $PKG_MGR install -y nodejs 2>/dev/null || true
        if ! command -v node > /dev/null; then
            sudo $PKG_MGR module -y enable nodejs:18 2>/dev/null || true
            sudo $PKG_MGR install -y nodejs npm 2>/dev/null || sudo $PKG_MGR install -y nodejs 2>/dev/null || true
        fi
        if ! command -v node > /dev/null && command -v nodejs > /dev/null; then
            sudo ln -sf "$(command -v nodejs)" /usr/local/bin/node || true
        fi

    elif [[ "$OS_ID" == "ubuntu" ]] || [[ "$OS_ID" == "debian" ]] || [[ "$OS_LIKE" == *"debian"* ]]; then
        NODE_MAJOR=$(get_node_major)
        if [ "$NODE_MAJOR" -lt 18 ] || ! command -v npm > /dev/null; then
            echo "閰嶇疆 NodeSource Node.js 18.x 浠撳簱..."
            curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
        fi

        sudo apt update -qq
        sudo apt install -y python3 python3-pip python3-venv git nginx bc nodejs lsof -qq
    else
        echo -e "${RED}涓嶆敮鎸佺殑绯荤粺: $OS${NC}"
        echo "璇锋墜鍔ㄥ畨瑁? git nginx python3(>=3.10) nodejs(>=18) npm lsof bc"
        exit 1
    fi
}

install_system_packages

# 鏍￠獙 Node.js (Vite 5 闇€瑕?>= 18)
if command -v node > /dev/null && command -v npm > /dev/null; then
    NODE_VER=$(node -v)
    NODE_MAJOR=$(get_node_major)
    if [ "$NODE_MAJOR" -lt 18 ]; then
        echo -e "${RED}Node.js 鐗堟湰杩囦綆: $NODE_VER (闇€瑕?>= 18)${NC}"
        exit 1
    fi
    echo -e "${GREEN}Node.js 宸插氨缁? $NODE_VER${NC}"
else
    echo -e "${RED}Node.js 瀹夎澶辫触锛屽墠绔棤娉曟瀯寤恒€?{NC}"
    exit 1
fi
# ================= 2. Python 鐜閰嶇疆 =================
echo -e "${YELLOW}[2/6] 閰嶇疆 Python 鐜...${NC}"

PYTHON_EXE=""
for callback in python3.12 python3.11 python3.10 python3; do
    if command -v $callback > /dev/null; then
        VER=$($callback -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        IS_OK=$(echo "$VER >= 3.10" | bc -l)
        if [ "$IS_OK" -eq 1 ]; then
            PYTHON_EXE=$callback
            echo "閫夊畾 Python: $PYTHON_EXE (鐗堟湰 $VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo -e "${RED}[Error] 鏈壘鍒?Python 3.10+銆?{NC}"
    exit 1
fi

# 閲嶅缓 venv
if [ -d "$VENV_DIR" ]; then rm -rf "$VENV_DIR"; fi
echo "鍒涘缓铏氭嫙鐜 ($VENV_DIR)..."
$PYTHON_EXE -m venv "$VENV_DIR"

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ================= 3. 閰嶇疆鏂囦欢 (.env) =================
WANT_ENV="y"
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}妫€娴嬪埌鐜版湁閰嶇疆鏂囦欢 (.env)${NC}"
    read -p "鏄惁闇€瑕侀噸鏂伴厤缃?AI 鏈嶅姟淇℃伅? [y/N] " WANT_ENV
fi

if [[ "$WANT_ENV" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}[閰嶇疆] 璇烽厤缃?AI 鏈嶅姟淇℃伅:${NC}"
    read -p "璇疯緭鍏?LLM API Key (鍥炶溅浣跨敤榛樿鍗犱綅绗?: " INPUT_KEY
    read -p "璇疯緭鍏?LLM Base URL (鍥炶溅浣跨敤榛樿: https://maas-api.cn-huabei-1.xf-yun.com/v2): " INPUT_URL
    read -p "璇疯緭鍏?LLM 妯″瀷 ID (鍥炶溅浣跨敤榛樿: xopglm47blth2): " INPUT_MODEL
    
    if [ -z "$INPUT_KEY" ]; then
        INPUT_KEY="your_key_here"
        echo "鏈緭鍏?Key锛屽皢浣跨敤榛樿鍗犱綅绗︺€傚悗缁偍鍙互閲嶆柊杩愯閮ㄧ讲鑴氭湰淇敼銆?
    fi
    
    if [ -z "$INPUT_URL" ]; then
        INPUT_URL="https://maas-api.cn-huabei-1.xf-yun.com/v2"
    fi
    
    if [ -z "$INPUT_MODEL" ]; then
        INPUT_MODEL="xopglm47blth2"
    fi

    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=$INPUT_KEY
LLM_BASE_URL=$INPUT_URL
LLM_MODEL_ID=$INPUT_MODEL
EOF
    echo -e "${GREEN}閰嶇疆鏂囦欢 (.env) 宸茬敓鎴愭垨鏇存柊銆?{NC}"
else
    echo "璺宠繃閲嶆柊閰嶇疆锛屼繚鐣欑幇鏈夌幆澧冮厤缃€?
fi

# ================= 3.1 绠＄悊鍛樿处鎴烽厤缃?=================
echo -e "${YELLOW}[3.1] 绠＄悊鍛樿处鎴烽厤缃?{NC}"
echo "鎻愮ず: 棣栨閮ㄧ讲寤鸿閰嶇疆銆傚鏋滆烦杩囷紝绯荤粺灏嗕繚鎸佹暟鎹簱鐜版湁绠＄悊鍛樼姸鎬併€?
read -p "鏄惁闇€瑕侀厤缃?閲嶇疆绠＄悊鍛樿处鎴? [y/N] " WANT_ADMIN
UPDATE_ADMIN="false"
ADMIN_USER_VAL="admin"
ADMIN_PASS_VAL="admin123"

if [[ "$WANT_ADMIN" =~ ^[Yy]$ ]]; then
    echo -e "璇烽€夋嫨鎿嶄綔:"
    echo "  1) 鎭㈠榛樿璁剧疆 (admin / admin123)"
    echo "  2) 璁剧疆鏂扮殑绠＄悊鍛?
    read -p "璇疯緭鍏ラ€夐」 [1/2]: " ADMIN_OPT
    
    if [ "$ADMIN_OPT" == "1" ]; then
        ADMIN_USER_VAL="admin"
        ADMIN_PASS_VAL="admin123"
        UPDATE_ADMIN="true"
        echo -e "${GREEN}宸查€夋嫨鎭㈠榛樿璁剧疆銆?{NC}"
    elif [ "$ADMIN_OPT" == "2" ]; then
        read -p "璇疯緭鍏ョ鐞嗗憳鐢ㄦ埛鍚?(榛樿 admin): " INPUT_USER
        ADMIN_USER_VAL=${INPUT_USER:-"admin"}
        
        while true; do
            read -s -p "璇疯緭鍏ョ鐞嗗憳瀵嗙爜: " INPUT_PASS
            echo ""
            read -s -p "璇峰啀娆¤緭鍏ュ瘑鐮? " INPUT_PASS2
            echo ""
            if [ "$INPUT_PASS" == "$INPUT_PASS2" ] && [ ! -z "$INPUT_PASS" ]; then
                ADMIN_PASS_VAL=$INPUT_PASS
                UPDATE_ADMIN="true"
                break
            else
                echo -e "${RED}瀵嗙爜涓嶅尮閰嶆垨涓虹┖锛岃閲嶈瘯銆?{NC}"
            fi
        done
        echo -e "${GREEN}宸茶缃柊绠＄悊鍛? $ADMIN_USER_VAL${NC}"
    else
        echo "鏈瘑鍒€夐」锛屽皢璺宠繃閰嶇疆銆?
    fi
else
    echo "璺宠繃绠＄悊鍛橀厤缃€?
fi

# ================= 4. 鍚庣渚濊禆 =================
echo -e "${YELLOW}[4/6] 瀹夎鍚庣渚濊禆...${NC}"
if [ -d ".git" ]; then git pull; fi
echo "姝ｅ湪瀹夎 Python 搴?.."
$VENV_PIP install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
$VENV_PIP install -r "$BACKEND_DIR/requirements.txt" -i https://mirrors.aliyun.com/pypi/simple/

# ================= 5. 鍓嶇鏋勫缓 =================
echo -e "${YELLOW}[5/6] 鏋勫缓鍓嶇璧勬簮...${NC}"
cd "$FRONTEND_DIR"

# 璁剧疆 npm 娣樺疂闀滃儚鍔犻€?
npm config set registry https://registry.npmmirror.com

echo "瀹夎鍓嶇渚濊禆..."
if [ ! -d "node_modules" ]; then
    npm install
else
    # 绠€鍗曠殑鍏ㄩ儴閲嶈鑰楁椂澶箙锛屽皾璇曠洿鎺?install
    npm install
fi

echo "缂栬瘧鍓嶇搴旂敤..."
# 灏濊瘯娑堥櫎 vue-tsc 鐗堟湰涓嶅吋瀹归棶棰? 濡傛灉鏋勫缓澶辫触锛屽皾璇曚粎浣跨敤 vite build
if ! npm run build; then
    echo -e "${YELLOW}鏍囧噯鏋勫缓澶辫触 (鍙兘鏄?vue-tsc 绫诲瀷妫€鏌ラ棶棰?锛屽皾璇曡烦杩囩被鍨嬫鏌ュ己鍒舵瀯寤?..${NC}"
    # 涓存椂浣跨敤 vite build - 涓轰簡绾噣杈撳嚭锛屾垜浠繖閲屼笉鎵撳嵃 tsc 淇℃伅
    # 鐩存帴杩愯 vite build (瀹冨簲璇ュ湪 PATH 涓紝濡傛灉涓嶅湪鍒欏皾璇?node_modules)
    if [ -f "./node_modules/.bin/vite" ]; then
        ./node_modules/.bin/vite build
    else
        npx vite build
    fi
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}鏋勫缓鍐嶆澶辫触!${NC}"
        echo "鎻愮ず: 濡傛灉鍑虹幇 'Killed' 閿欒锛屾鏌?Swap銆?
        exit 1
    fi
fi

# ================= 6. 鍚姩鏈嶅姟 =================
echo -e "${YELLOW}[6/6] 鍚姩鏈嶅姟...${NC}"

# Python 绔彛妫€鏌?
check_port() {
    $VENV_PYTHON -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); exit(0 if s.connect_ex(('127.0.0.1', $1)) != 0 else 1)"
    return $?
}

for ((i=0; i<3; i++)); do
    # 灏濊瘯kill浠ュ墠鍙兘娈嬬暀鐨勫悓鍚嶆湇鍔?(鏋佸叾绠€鍗曠殑闃插爢绉€昏緫)
    # 娉ㄦ剰: 杩欓噷浠呮潃姝诲叧鑱斿埌褰撳墠鐩綍鐨?uvicorn
    pkill -f "$PROJECT_DIR/backend/venv/bin/uvicorn" 2>/dev/null
done

DEFAULT_PORT=8000
PORT=$DEFAULT_PORT

for ((i=0; i<10; i++)); do
    if check_port $PORT; then
        echo -e "${GREEN}浣跨敤绔彛: $PORT${NC}"
        break
    else
        echo "绔彛 $PORT 琚崰鐢紝灏濊瘯 $((PORT+1))..."
        PORT=$((PORT+1))
    fi
done

cd "$BACKEND_DIR"

# 鍚敤 Python 鏃犵紦鍐叉ā寮忥紝纭繚鏃ュ織瀹炴椂鍐欏叆
export PYTHONUNBUFFERED=1

# 杩愯鏁版嵁搴撳崌绾т笌绠＄悊鍛樿缃?
echo "搴旂敤鏁版嵁搴撳彉鏇翠笌绠＄悊鍛樻潈闄?.."
UPDATE_ADMIN="$UPDATE_ADMIN" ADMIN_USER="$ADMIN_USER_VAL" ADMIN_PASS="$ADMIN_PASS_VAL" "$VENV_PYTHON" upgrade_admin.py

# 鐢熶骇鐜寤鸿鍘绘帀 --reload锛屽寮虹ǔ瀹氭€?
echo "鍚姩鍚庣鏈嶅姟 (Port: $PORT)..."
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port $PORT >> "$PROJECT_DIR/backend.log" 2>&1 &
PID=$!

sleep 5  # 澧炲姞绛夊緟鏃堕棿锛岀‘淇濆畬鍏ㄥ惎鍔ㄦ垨鎶ラ敊閫€鍑?
if ps -p $PID > /dev/null; then
    IP=$(hostname -I | awk '{print $1}')
    
    echo -e "${YELLOW}姝ｅ湪鍚姩鍓嶇鏈嶅姟 (绔彛: $FRONTEND_PORT)...${NC}"
    
    # ----------------------------------------------------
    # 浣跨敤 Node.js + Express 鎼缓绠€鏄撶敓浜х幆澧冧唬鐞嗘湇鍔″櫒
    # 瑙ｅ喅 serve 鏃犳硶浠ｇ悊 /api 璇锋眰瀵艰嚧鐨?404/undefined 闂
    # ----------------------------------------------------
    
    echo "瀹夎鐢熶骇鐜鏈嶅姟渚濊禆 (express, http-proxy-middleware)..."
    cd "$FRONTEND_DIR"
    npm install express http-proxy-middleware --no-save

    # 鐢熸垚 server.cjs (浣跨敤 .cjs 閬垮厤 type: module 闂)
    cat > server.cjs <<EOF
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const app = express();

const BACKEND_PORT = $PORT;
const FRONTEND_PORT = $FRONTEND_PORT;
const API_URL = "http://127.0.0.1:" + BACKEND_PORT;

console.log("鍚姩鍓嶇鏈嶅姟鍣?..");
console.log("浠ｇ悊鐩爣:", API_URL);

// 1. 閰嶇疆 API 浠ｇ悊 (涓?vite.config.ts 閫昏緫淇濇寔涓€鑷?
app.use('/api', createProxyMiddleware({ 
    target: API_URL, 
    changeOrigin: true,
    xfwd: true, // Auto-add x-forwarded-for headers so backend sees real IP
    pathRewrite: { '^/api': '' },
    proxyTimeout: 600000, // 10鍒嗛挓瓒呮椂锛岄槻姝?AI 鐢熸垚杩囩▼涓柇
    timeout: 600000,      // 浼犲叆杩炴帴瓒呮椂
    onProxyReq: (proxyReq, req, res) => {
        // Keeps socket alive
        proxyReq.setTimeout(600000);
    },
    onError: (err, req, res) => {
        console.error('Proxy Error:', err);
        res.status(500).send('Proxy Error');
    }
}));

// 2. 鎵樼闈欐€佹枃浠?(dist)
app.use(express.static(path.join(__dirname, 'dist')));

// 3. SPA 鍥為€€ (鎵€鏈夊叾浠栬姹傝繑鍥?index.html)
app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(FRONTEND_PORT, '0.0.0.0', () => {
  console.log(\`Frontend service running at http://0.0.0.0:\${FRONTEND_PORT}\`);
});
EOF

    # 娓呯悊鏃х殑鍓嶇杩涚▼ (濡傛灉鏈?
    fpid=$(lsof -t -i:$FRONTEND_PORT)
    if [ -n "$fpid" ]; then
        kill -9 $fpid
    fi
    
    # 鍚姩 Node 鏈嶅姟
    nohup node server.cjs > "$PROJECT_DIR/frontend.log" 2>&1 &

    cat > "$RUNTIME_FILE" <<EOF
PROJECT_DIR=$PROJECT_DIR
BACKEND_DIR=$BACKEND_DIR
FRONTEND_DIR=$FRONTEND_DIR
VENV_DIR=$VENV_DIR
BACKEND_PORT=$PORT
FRONTEND_PORT=$FRONTEND_PORT
BACKEND_LOG=$PROJECT_DIR/backend.log
FRONTEND_LOG=$PROJECT_DIR/frontend.log
EOF

    chmod +x "$PROJECT_DIR/miaobi" "$PROJECT_DIR/update.sh" "$PROJECT_DIR/uninstall.sh"
    ln -sf "$PROJECT_DIR/miaobi" /usr/local/bin/miaobi 2>/dev/null || true
    
    echo -e "\n${GREEN}====== 閮ㄧ讲鎴愬姛 ======${NC}"
    echo -e "鍓嶇璁块棶鍦板潃:  http://$IP:$FRONTEND_PORT"
    echo -e "鍚庣 API 鍦板潃: http://$IP:$PORT"
    echo -e "--------------------------------------------------------"
    echo -e "鍓嶇鏃ュ織:      tail -f $PROJECT_DIR/frontend.log"
    echo -e "鍚庣鏃ュ織:      tail -f $PROJECT_DIR/backend.log"
    echo -e "--------------------------------------------------------"
    echo -e "${YELLOW}閲嶈鎻愮ず: 璇风‘淇濅簯鏈嶅姟鍣ㄥ畨鍏ㄧ粍/闃茬伀澧欏凡鏀捐绔彛: $PORT (鍚庣) 鍜?$FRONTEND_PORT (鍓嶇)${NC}"
else
    echo -e "${RED}鍚庣鍚姩澶辫触锛岃鏌ョ湅 backend.log${NC}"
    exit 1
fi
