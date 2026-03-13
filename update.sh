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
FRONTEND_SERVER_FILE="$FRONTEND_DIR/server.cjs"
RUNTIME_FILE="$PROJECT_DIR/.lumina_runtime"
BACKEND_PORT="8000"
FRONTEND_PORT="8600"
BACKEND_LOG="$PROJECT_DIR/backend.log"
FRONTEND_LOG="$PROJECT_DIR/frontend.log"

if [ -f "$RUNTIME_FILE" ]; then
    # shellcheck disable=SC1090
    source "$RUNTIME_FILE"
fi

mkdir -p "$BACKUP_DIR"

echo -e "${BLUE}====== 濠碘剝鐟ч悷顏劽规担绋垮辅濞戞挴鍋撻梺娆惧枟濞插潡寮幏灞藉闁?======${NC}"
echo "濡炪倕婀卞ú浼存儎椤旇偐绉? $PROJECT_DIR"

backup_if_exists() {
    local source_path="$1"
    local relative_path=""
    local target_dir=""

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
    echo "鐎瑰憡褰冮ˇ顒佺? $source_path"
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
        frontend/server.cjs|frontend/package-lock.json)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

echo -e "${YELLOW}[1/6] 婵☆偀鍋撻柡灞诲劚缂嶅宕滃鍕暕闁活喕鑳舵慨鎼佸箑?..${NC}"
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo -e "${RED}鐟滅増鎸告晶鐘绘儎椤旇偐绉垮☉鎾崇У濡?Git 濞寸姵鎸哥花閬嶆晬鐏炵偓锟ユ繛澶嬫礃婢х晫鎮扮仦鐐函闁哄倽鍩囬埀?{NC}"
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
    echo "婵☆偀鍋撴繛鏉戭儏閸╁矂鏌堥妸褑顔夐弶鈺佹川閳诲吋绂嶈閺佹捇鎯冮崟顒佹嫳闁革妇澧楅弸鍐╃鐠哄搫缍侀柡鍥彧缁辨繂顫㈤敐鍛含闁煎浜滄慨鈺傚緞閸ワ箑鏁滄鐐跺煐娴狀喗寰?Git 闁绘鍩栭埀?.."
    for path in "${AUTO_RESTORE_PATHS[@]}"; do
        backup_if_exists "$PROJECT_DIR/$path"
        git restore --source=HEAD --worktree --staged -- "$path"
        echo "鐎圭寮舵禒顔藉緞? $path"
    done
fi

if [ "${#BLOCKING_CHANGES[@]}" -gt 0 ]; then
    echo -e "${RED}婵☆偀鍋撴繛鏉戭儏閸╁本绂掗敐鍥╁灣闁瑰瓨鐗犻崢銈囩磾椤旇偐鎽犻柛锔哄妽濠€顓㈠箵閹邦亝鍞夊ǎ鍥跺枟閺佸ジ濡撮崒娆掔闂侇剙鐏濋崢銈囨啺閸℃瑦纾板ù婊冩惈娴兼劙寮ㄩ悷鏉啃楅柨娑樻湰濠€鏉库枎閳╁啯绾柡鍌涙緲閸戯繝宕戝鍕靛壘闁?{NC}"
    printf '%s\n' "${BLOCKING_CHANGES[@]}"
    echo "閻犲洤鍢查崢娑氭兜椤旀鍚囬弶鈺傜懁缁ㄧ儤绌遍鑺ユ毉闁哄嫷鍨伴幆渚€妫侀埀顒傛啺娴ｉ绠介柣锝嗙懀閳?
    exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ -z "$CURRENT_BRANCH" ] || [ "$CURRENT_BRANCH" = "HEAD" ]; then
    echo -e "${RED}鐟滅増鎸告晶鐘崇▔瀹ュ懏韬柡鍫濐槹閺呫儵宕氶崱妯绘殰濞戞挸顭槐婵嬪籍閻樺磭銆婇悗鐟邦槸閸欏繘骞忔径濠傜悼闁哄洤鐡ㄩ弻濠囧Υ?{NC}"
    exit 1
fi

echo "鐟滅増鎸告晶鐘诲礆閸℃ɑ鏆? $CURRENT_BRANCH"

echo -e "${YELLOW}[2/6] 濠㈣泛娲ｉ崬銈夋偨閵婏箑鐓曢柡浣哄瀹撲線宕畝鍕赋缂?..${NC}"
backup_if_exists "$BACKEND_DIR/.env"
backup_if_exists "$BACKEND_DIR/lumina.db"
backup_if_exists "$BACKEND_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/lumina_v2.db"
backup_if_exists "$PROJECT_DIR/backend.log"
backup_if_exists "$PROJECT_DIR/frontend.log"
backup_if_exists "$RUNTIME_FILE"

echo -e "${YELLOW}[3/6] 闁瑰嘲顦ぐ鍥嫉閳ь剟寮０浣告暕闁?..${NC}"
git fetch origin
git pull --ff-only origin "$CURRENT_BRANCH"

echo -e "${YELLOW}[4/6] 闁哄洤鐡ㄩ弻濠囧触鎼达綆浼傚〒姘箚缁?..${NC}"
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}闁哄牜浜濇竟姗€宕氶弶鍨闁活潿鍔庡▓?Python 3闁?{NC}"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "闁哄牜浜濇竟姗€宕氶幏灞剧彄闁归鍠撻獮鍡樻櫠閸愯法绀夋慨婵撶到濠€顏堝礆濞戞绱?.."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PIP" install -r "$BACKEND_DIR/requirements.txt"

echo -e "${YELLOW}[5/6] 闁哄瀚紓鎾诲礈瀹ュ浂浼?..${NC}"
cd "$FRONTEND_DIR"
if [ -f "$FRONTEND_DIR/package-lock.json" ]; then
    if ! npm ci; then
        echo -e "${YELLOW}npm ci 濠㈡儼绮剧憴锕傛晬鐏炶姤绀€闂侇偀鍋撻柛?npm install闁?{NC}"
        npm install
    fi
else
    npm install
fi

if ! npm run build; then
    echo -e "${YELLOW}婵☆偀鍋撴繛鏉戭儏閸?npm run build 濞戞挸绉磋ぐ鏌ユ偨椤帞绀夐柤濂変簻婵晠宕堕悙琛″亾閳ь剟宕?vite build闁?{NC}"
    npx vite build
fi

echo -e "${YELLOW}[6/6] 闂佹彃绉撮幆搴ゃ亹閹惧啿顤呴柡鍫濈Т婵?..${NC}"
cd "$PROJECT_DIR"

if [ -f "$PROJECT_DIR/miaobi" ]; then
    chmod +x "$PROJECT_DIR/miaobi" "$PROJECT_DIR/update.sh" "$PROJECT_DIR/uninstall.sh"
    "$PROJECT_DIR/miaobi" restart
else
    echo -e "${YELLOW}闁哄牜浜濇竟姗€宕?miaobi闁挎稑濂旀繛鍥偨閵娿儱鎮戦悗纭咁潐鑶╃€殿喖绻橀崳鎼佸触椤栨稒绠涢柛鏂呮壋鍋?{NC}"

    if [ -x "$VENV_DIR/bin/uvicorn" ]; then
        pkill -f "$VENV_DIR/bin/uvicorn" 2>/dev/null || true
        cd "$BACKEND_DIR"
        nohup "$VENV_DIR/bin/uvicorn" main:app --app-dir "$BACKEND_DIR" --host 0.0.0.0 --port "$BACKEND_PORT" >> "$BACKEND_LOG" 2>&1 &
        cd "$PROJECT_DIR"
    fi

    if [ -f "$FRONTEND_SERVER_FILE" ]; then
        fpid="$(lsof -t -i:$FRONTEND_PORT 2>/dev/null || true)"
        if [ -n "${fpid:-}" ]; then
            kill -9 "$fpid" 2>/dev/null || true
        fi
        nohup node "$FRONTEND_SERVER_FILE" >> "$FRONTEND_LOG" 2>&1 &
    fi
fi

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

if [ -f "$PROJECT_DIR/miaobi" ]; then
    chmod +x "$PROJECT_DIR/miaobi" "$PROJECT_DIR/update.sh" "$PROJECT_DIR/uninstall.sh"
    rm -f /usr/local/bin/miaobi
    install -m 755 "$PROJECT_DIR/miaobi" /usr/local/bin/miaobi 2>/dev/null || cp -f "$PROJECT_DIR/miaobi" /usr/local/bin/miaobi
fi

echo -e "${GREEN}闁哄洤鐡ㄩ弻濠勨偓鐟版湰閸ㄦ岸濡撮崒姘兼У濞寸姷鏅ú鎷屻亹? $BACKUP_DIR${NC}"
echo "闁稿绮嶉娑㈠嫉瀹ュ懎顫ら柛娑欏灊閹? miaobi stop"
