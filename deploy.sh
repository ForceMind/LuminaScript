#!/bin/bash

# ==========================================
# LuminaScript 闂佸搫鎳樼紓姘跺礂濮椻偓閺屽牓濡歌椤斿鏌涢弬鐑樻珕濠?(闂佺绻堥崝宥夋偉閵堝鍋?
# ==========================================

# 婵☆偆澧楃划蹇旂珶婵犲嫧鍋撶憴鍕叝缂?
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

# ================= 闂備焦婢樼粔鍫曟偪閸℃稑绀?=================
# 闂侀潻璐熼崝蹇曟崲閺嶎厽鐓傜€光偓閸愭儳鏅紓鍌氬枤閸犳宕戝澶婂珘闁绘柨鎲＄粻鍧楁煟閵娿儱顏╁褏濮风划鈺咁敍濠垫劕鏅遍梻鍌氬亞閸犳岸顢旈鍕煑?
FRONTEND_PORT=8600
# ========================================

echo -e "${BLUE}====== 婵犵鍓濋悷褔鎮烽鍔借鎷呯粙鍨緟 (LuminaScript) 闂備緡鍠撻崝搴ｆ媼閺屻儱绀夐柍鍝勫€搁·?======${NC}"

# ================= 0. 闂佸憡鍔曢幊搴ㄦ偤閵婏箑顕辨俊顖氭惈椤?(闂佺厧顨庢禍婊勬叏?SWAP) =================
# 闁荤喐鐟辩徊浠嬪窗閸涱喗濯撮幖娣妼鐢娊鏌￠崼婵埿㈠┑顔惧枛瀹曟娊濡搁妸褏顓奸柣?dnf/yum/pip/npm 闂佸搫鍟晶搴♀枔?"Killed" 闂傚倸鍋嗛崳锝夈€?
check_swap() {
    SWAP_SIZE=$(free -m | grep Swap | awk '{print $2}')
    if [ "$SWAP_SIZE" -eq 0 ]; then
        echo -e "${YELLOW}[0/6] 濠碘槅鍋€閸嬫挻绻涢弶鎴剰闁糕晛鐭傚?Swap闂佹寧绋戦張顒勵敆濠婂牆鎹堕柕濞垮劚閻忥紕鈧?2GB 婵炴垶鎸搁悺銊ヮ渻?Swap 婵炲濮伴崕闈浳?OOM...${NC}"
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q "/swapfile" /etc/fstab; then
            echo "/swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}Swap 闂佸憡甯楃粙鎴犵磽閹捐绠ｉ柟閭﹀墮椤?${NC}"
    else
        echo "濠碘槅鍋€閸嬫挻绻涢弶鎴剰闁?Swap: ${SWAP_SIZE}MB (闁荤姴鎼悿鍥╂崲閸愵喖绀嗘繛鎴烆焽缁?"
    fi
}
if [ "$EUID" -eq 0 ]; then
    check_swap
else
    echo -e "${YELLOW}闂?root 闂佹椿娼块崝宥夊春濞戞瑦浜ら柟閭﹀灱閺€浠嬫煥濞戞鐒烽柣鎴檮濞?Swap 闂佺厧顨庢禍婊勬叏閳哄懎绀嗘繛鎴烆焽缁憋箓鏌?{NC}"
fi

# ================= 1. 缂備緡鍨靛畷鐢靛垝閻戞鐟规繝闈涳功椤╊偊鎮楅悷閭︽Ъ妞?(闂?Node.js) =================
echo -e "${YELLOW}[1/6] 濠碘槅鍋€閸嬫捇鏌＄仦璇插姎闁艰崵鍠撻埀顒傛嚀椤︽娊藟婵犲嫬瀵查柤濮愬€楅崺鐘层€掑顓犵畾缂?(Python & Node.js)...${NC}"

OS="Unknown"
OS_ID=""
OS_LIKE=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS="${NAME:-Unknown}"
    OS_ID=$(echo "${ID:-}" | tr '[:upper:]' '[:lower:]')
    OS_LIKE=$(echo "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]')
fi
echo "閻熸粎澧楅幐鍛婃櫠閻橀潧瀵查柤濮愬€楅崺? $OS"
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
            echo "闂備焦婢樼粔鍫曟偪?NodeSource Node.js 18.x 婵炲濮甸幐鍝ヨ姳?.."
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
            echo "闂備焦婢樼粔鍫曟偪?NodeSource Node.js 18.x 婵炲濮甸幐鍝ヨ姳?.."
            curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
        fi

        sudo apt update -qq
        sudo apt install -y python3 python3-pip python3-venv git nginx bc nodejs lsof -qq
    else
        echo -e "${RED}婵炴垶鎸哥粔鐢稿极椤曗偓楠炴劖鎷呴悜妯兼殸缂備緡鍨靛畷鐢靛垝? $OS${NC}"
        echo "闁荤姴娲弨杈ㄦ櫠濠婂牆绀夐柕濞垮劤閺嗘棃鎮? git nginx python3(>=3.10) nodejs(>=18) npm lsof bc"
        exit 1
    fi
}

install_system_packages

# 闂佸搫绋勭换婵嬫偘?Node.js (Vite 5 闂傚倸娲犻崑鎾绘偡?>= 18)
if command -v node > /dev/null && command -v npm > /dev/null; then
    NODE_VER=$(node -v)
    NODE_MAJOR=$(get_node_major)
    if [ "$NODE_MAJOR" -lt 18 ]; then
        echo -e "${RED}Node.js 闂佺粯顨呴悧濠傦耿閻楀牊浜ら柛銉缁? $NODE_VER (闂傚倸娲犻崑鎾绘偡?>= 18)${NC}"
        exit 1
    fi
    echo -e "${GREEN}Node.js 閻庣懓鎲¤ぐ鍐潩閵娧呯＜? $NODE_VER${NC}"
else
    echo -e "${RED}Node.js 闁诲海鎳撻ˇ鎶剿夋繝鍐ㄧ窞閺夊牜鍋夎闂佹寧绋戦懟顖涙櫠閻樼數鍗氭い鏍ㄧ⊕閿熴儲绻涙径瀣闁诲寒鍨伴娆撳箒閹哄棗浜?{NC}"
    exit 1
fi
# ================= 2. Python 闂佺粯绮犻崹浼淬€傞妸鈺傜厐鐎广儱娲ㄩ弸?=================
echo -e "${YELLOW}[2/6] 闂備焦婢樼粔鍫曟偪?Python 闂佺粯绮犻崹浼淬€?..${NC}"

PYTHON_EXE=""
for callback in python3.12 python3.11 python3.10 python3; do
    if command -v $callback > /dev/null; then
        VER=$($callback -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        IS_OK=$(echo "$VER >= 3.10" | bc -l)
        if [ "$IS_OK" -eq 1 ]; then
            PYTHON_EXE=$callback
            echo "闂備緡鍋勯ˇ顖炴偩?Python: $PYTHON_EXE (闂佺粯顨呴悧濠傦耿?$VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo -e "${RED}[Error] 闂佸搫鐗滄禍婵囩珶濮椻偓瀹?Python 3.10+闂?{NC}"
    exit 1
fi

# 闂備焦褰冪粔瀵哥磽?venv
if [ -d "$VENV_DIR" ]; then rm -rf "$VENV_DIR"; fi
echo "闂佸憡甯楃粙鎴犵磽閹剧粯鎯炴慨姗嗗幖閻濐垶鏌ｅ搴＄仩妞?($VENV_DIR)..."
$PYTHON_EXE -m venv "$VENV_DIR"

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ================= 3. 闂備焦婢樼粔鍫曟偪閸℃稑妫橀柛銉檮椤?(.env) =================
WANT_ENV="y"
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}濠碘槅鍋€閸嬫挻绻涢弶鎴剰闁糕晛鐭傞幃鎶芥偋閸喓鐣抽梻浣规緲缁夊爼鎮块崱娑樻闁搞儻闄勯?(.env)${NC}"
    read -p "闂佸搫瀚烽崹浼村箚娓氣偓濡線鍩€椤掑倹鍟哄〒姘ｅ亾闁革絾鎮傚顒佸閺夋垵璧嬬紓?AI 闂佸搫鐗嗙粔瀛樻叏閻斿摜鈹嶉柍鈺佸暕缁? [y/N] " WANT_ENV
fi

if [[ "$WANT_ENV" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}[闂備焦婢樼粔鍫曟偪閸?闁荤姴娲ㄩ崗姗€宕㈤妶鍥╃＞?AI 闂佸搫鐗嗙粔瀛樻叏閻斿摜鈹嶉柍鈺佸暕缁?${NC}"
    read -p "闁荤姴娲ㄩ弻澶屾椤撱垹绀?LLM API Key (闂佹悶鍎抽崑鐘测攦閸涱喗濯撮悹鎭掑妽閺嗗繐顫楀☉娆樼劸妞ゆ挸顭峰畷锟犳偐鏉堚晝孝缂?: " INPUT_KEY
    read -p "闁荤姴娲ㄩ弻澶屾椤撱垹绀?LLM Base URL (闂佹悶鍎抽崑鐘测攦閸涱喗濯撮悹鎭掑妽閺嗗繐顫楀☉娆樼劸妞? https://maas-api.cn-huabei-1.xf-yun.com/v2): " INPUT_URL
    read -p "闁荤姴娲ㄩ弻澶屾椤撱垹绀?LLM 濠碘槅鍨埀顒€纾埀?ID (闂佹悶鍎抽崑鐘测攦閸涱喗濯撮悹鎭掑妽閺嗗繐顫楀☉娆樼劸妞? xopglm47blth2): " INPUT_MODEL
    
    if [ -z "$INPUT_KEY" ]; then
        INPUT_KEY="your_key_here"
        echo "闂佸搫鐗滄禍锝囨椤撱垹绀?Key闂佹寧绋戦懟顖炴儍閵忊剝濯撮悹鎭掑妽閺嗗繐顫楀☉娆樼劸妞ゆ挸顭峰畷锟犳偐鏉堚晝孝缂備焦顨愮拹鐔煎焵椤掆偓閸婃悂骞冨Δ鍐＜妞ゆ挾鍠庢禍鍫曟煕濞嗘ê鐏熷ù婊勫浮閺屽苯顓奸崱妯煎帎闁哄鏅滈崝姗€銆侀幋锔界劸闁靛ě鍡╁敼闂佺厧鐡ㄧ喊宥咃耿閻楀牏鈹嶆い鏃囧Г閺嗩參鏌?
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
    echo -e "${GREEN}闂備焦婢樼粔鍫曟偪閸℃稑妫橀柛銉檮椤?(.env) 閻庤鐡曠亸娆撳极閹捐绠ｉ柟閭﹀幖閻忔鏌￠崶褏鎽犻柡灞斤躬婵?{NC}"
else
    echo "闁荤姴鎼悿鍥╂崲閸愵喗鐓傜€广儱妫欓悡鈧梻浣规緲缁夊爼鎮块崱娑欐櫖閻忕偠鍋愮粻浠嬫煟閿濆棛鎳呮鐐叉喘瀵灚寰勭仦鐣岀暰婵犫拃鍐ㄦ殻闁告ǜ鍊楃槐鏃堫敊鐞涒€充壕?
fi

# ================= 3.1 缂備胶濯寸槐鏇㈠箖婵犲洤宸濇俊顖氱仢椤︹晠鏌熺€电鍘撮柛妯稿€楃槐?=================
echo -e "${YELLOW}[3.1] 缂備胶濯寸槐鏇㈠箖婵犲洤宸濇俊顖氱仢椤︹晠鏌熺€电鍘撮柛妯稿€楃槐?{NC}"
echo "闂佸湱绮崝妤呭Φ? 婵☆偓绲鹃悧妤咁敃婵傚憡鐒鹃柕濞у棭鍞归悗鐐瑰€濈紓姘额敊閸涘瓨鐓€鐎广儱娲ㄩ弸鍌炴煏閸℃鈧悂銆呰瀵顭ㄩ崱娆忓姕闁哄鏅涘ù鍕濠靛牆瀵查柤濮愬€楅崺鐘绘倶韫囨挻顥欑紒缁樺哺楠炴劖鎷呯憴鍕啀闂佺顕栭崰鏍姳闁秵鍋濋柣妤€鐗婄粻鎺旂磼閻欏懐纾块柟顔硷躬瀹曘劌螣閻撳巩锕傛煙椤戝潡妾烽柍?
read -p "闂佸搫瀚烽崹浼村箚娓氣偓濡線鍩€椤掑倹鍟哄〒姘ｅ亾闁告ǜ鍊楃槐?闂備焦褰冪粔鍫曟偪閸℃瑧涓嶉柨娑樺閸婄偤鏌涘☉娆樼劸濠㈣泛瀚伴獮? [y/N] " WANT_ADMIN
UPDATE_ADMIN="false"
ADMIN_USER_VAL="admin"
ADMIN_PASS_VAL="admin123"

if [[ "$WANT_ADMIN" =~ ^[Yy]$ ]]; then
    echo -e "闁荤姴娲ㄩ崗姗€鍩€椤掆偓椤︽壆鈧哎鍔戦獮娆忣吋閸曨厾鈻?"
    echo "  1) 闂佽鍘归崹褰捤囬崣澶樻付婵☆垱顑欓崥鍥偣娴ｇ鈷旈柣?(admin / admin123)"
    echo "  2) 闁荤姳绀佹晶浠嬫偪閸℃稑妫橀柟娈垮枟閻ｈ京绱掗悪鍛？闁诡喖锕畷?
    read -p "闁荤姴娲ㄩ弻澶屾椤撱垹绀傞柕澶嗘杹閸嬫挻寰勯幇鈹惧亾?[1/2]: " ADMIN_OPT
    
    if [ "$ADMIN_OPT" == "1" ]; then
        ADMIN_USER_VAL="admin"
        ADMIN_PASS_VAL="admin123"
        UPDATE_ADMIN="true"
        echo -e "${GREEN}閻庣懓鎲￠悡锟犲焵椤掆偓椤︽壆鈧哎鍔戦獮渚€濮€閻欌偓濡茶顫楀☉娆樼劸妞ゆ捁宕甸幏瀣礈瑜忛弸鍌炴煏?{NC}"
    elif [ "$ADMIN_OPT" == "2" ]; then
        read -p "闁荤姴娲ㄩ弻澶屾椤撱垹绀傞柕澹苯鎮侀梺鑽ゅ仜濡骞夐幎鑺ュ仺闁靛绠戦悡鏇㈡煕?(婵帗绋掗…鍫ヮ敇?admin): " INPUT_USER
        ADMIN_USER_VAL=${INPUT_USER:-"admin"}
        
        while true; do
            read -s -p "闁荤姴娲ㄩ弻澶屾椤撱垹绀傞柕澹苯鎮侀梺鑽ゅ仜濡骞夊畷鍥ｅ亾闂堟稒顥犻柣? " INPUT_PASS
            echo ""
            read -s -p "闁荤姴娲ら崲鏌ュ疮閳ь剚绻涢崰掳鍊楃紙濠氭煕韫囧鍔氶柣锔藉灴閹? " INPUT_PASS2
            echo ""
            if [ "$INPUT_PASS" == "$INPUT_PASS2" ] && [ ! -z "$INPUT_PASS" ]; then
                ADMIN_PASS_VAL=$INPUT_PASS
                UPDATE_ADMIN="true"
                break
            else
                echo -e "${RED}闁诲酣娼уΛ娑㈡偉濠婂嫮鈻旂€广儱鎳庨悥閬嶆⒑閺夎法校闁搞劊鍔嶇粙澶愭惞閸忓鏅為梺鎸庣☉閻線顢氶鍕厒鐎广儱鐗忓Σ鎼佹煏?{NC}"
            fi
        done
        echo -e "${GREEN}閻庤鐡曠亸顏堫敊閺囩姷纾炬い鏃囧Г閻撯偓缂備胶濯寸槐鏇㈠箖婵犲洤宸? $ADMIN_USER_VAL${NC}"
    else
        echo "闂佸搫鐗滄禍锝夋儊閹达箑绀嗘い鎰惰吂閸嬫挻寰勯幇鈹惧亾瀹ュ鏅悘鐐垫櫕濞堟椽鎮归崫鍕瀮缂佽鍟撮弻濠傤吋閸モ晜鐎梺?
    fi
else
    echo "闁荤姴鎼悿鍥╂崲閸愵亞涓嶉柨娑樺閸婄偤鏌涘☉娆欒€块柛妯稿€楃槐鏃堫敊鐞涒€充壕?
fi

# ================= 4. 闂佸憡鑹惧ù鐑筋敂椤掍胶鐟规繝闈涳功椤?=================
echo -e "${YELLOW}[4/6] 闁诲海鎳撻ˇ鎶剿夋繝鍥цЕ閹艰揪缍嗘导鍌氥€掑顓犵畾缂?..${NC}"
if [ -d ".git" ]; then git pull; fi
echo "濠殿喗绻愮徊钘夛耿椤忓棌鍋撻悷閭︽Ъ妞?Python 闁?.."
$VENV_PIP install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
$VENV_PIP install -r "$BACKEND_DIR/requirements.txt" -i https://mirrors.aliyun.com/pypi/simple/

# ================= 5. 闂佸憡鎸哥粔鍫曨敂椤掑嫬鍑犻柛鏇ㄥ亞缁?=================
echo -e "${YELLOW}[5/6] 闂佸搫顑呯€氼剛绱撻幘璇茬鐎广儱娴傛导鍌炴偣瑜嶇€氼厾鑺?..${NC}"
cd "$FRONTEND_DIR"

# 闁荤姳绀佹晶浠嬫偪?npm 濠电儑绲鹃敋闁汇倕鍊垮鐟邦煥閸愩劌澹栭梺鍛婃⒒婵炩偓闁?
npm config set registry https://registry.npmmirror.com

echo "闁诲海鎳撻ˇ鎶剿夋繝鍥х鐎广儱娴傛导鍌氥€掑顓犵畾缂?.."
if [ ! -d "node_modules" ]; then
    npm install
else
    # 缂備胶濮崑鎾绘煕濡や焦绀堟繛鍫熷灴瀹曟濡搁埡浣稿Г闂備焦褰冪粔鐑剿夋繝鍥ㄥ殌婵°倓鐒﹂ˇ褍顭挎０婵呯胺缂佺姵鐟╅弫宥囦沪閻ｅ本顕涢柣鐘叉处濞叉粌煤閸ф绠?install
    npm install
fi

echo "缂傚倸鍊归悧鐐烘儊瑜斿畷婊冾吋閸ユ湹绱撻柟鐓庣摠濮婂寮?.."
# 闁诲繐绻戠换鍡涙儊椤栨埃妲堥柛顐犲劜閻?vue-tsc 闂佺粯顨呴悧濠傦耿閻楀牏鈻旂€广儱鎳庨幃鎴︽倵閻熸壆绉烘俊顐ュ煐閿? 婵犵鈧啿鈧綊鎮樻径鎰嚑闁告洦鍋嗙槐锕€顭块幆鎵翱閻熸瑱绠撻弫宥囦沪閻ｅ本顕涢柣鐘叉处濞叉垹鍒掓惔銏″閻犳亽鍔嶉弳?vite build
if ! npm run build; then
    echo -e "${YELLOW}闂佸搫绉村ú銈夊闯椤栫偛鍑犻柛鏇ㄥ亞缁憋箑顭块幆鎵翱閻?(闂佸憡鐟崹鐢稿礂濮椻偓瀵?vue-tsc 缂備緡鍋夐褔鎮楀畡鎷旀盯鍩€椤掑嫬钃熼柕澶嗘櫆閿涙牕螞?闂佹寧绋戦懟顖炴儍閸撗勫珰闁哄浄绱曢崕鏌ュ级閳轰焦宸濋悶姘煎亰瀹曞湱鈧綆鍋掑楣冩煛鐏炶鍔氱€规悂浜跺畷姘舵嚋閻㈢鍋撻姘煎殘?..${NC}"
    # 婵炴垶鎸搁悺銊ヮ渻閸屾稒濯撮悹鎭掑妽閺?vite build - 婵炴垶鎹佸銊ц姳閿涘嫮妫い鏍ㄨ壘濞呭秹寮堕崼鐔稿碍闁搞値鍙冮弫宥囦沪閹呬函婵炲濯崜婵堟崲閺嶎厽鐓傞悘鐐跺亹閻熸繈鏌熼崹顐ｅ碍鐎?tsc 婵烇絽娲犻崜婵囧?
    # 闂佺儵鏅涢悺銊ф暜鐎涙ɑ浜ら柟閭﹀灱閺€?vite build (闁诲海鎳撻崯顐よ姳閼碱剚瀚氶柕澶堝劜闊?PATH 婵炴垶鎼╅崣蹇曟濠靛洣绻嗛柛灞剧〒娴滎垰鈽夐幘宕囆㈡繝鈧鍫濈婵炲棗绻掑В锕傛偣?node_modules)
    if [ -f "./node_modules/.bin/vite" ]; then
        ./node_modules/.bin/vite build
    else
        npx vite build
    fi
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}闂佸搫顑呯€氼剛绱撻幘璇茬鐎广儱妫崑褍顭块幆鎵翱閻?${NC}"
        echo "闂佸湱绮崝妤呭Φ? 婵犵鈧啿鈧綊鎮樻径鎰闁惧繒鎳撶粻?'Killed' 闂備焦瀵ч悷銊╊敋閵堝鏅悘鐐佃檸濮婇箖鏌?Swap闂?
        exit 1
    fi
fi

# ================= 6. 闂佸憡鍑归崹鐗堟叏閳哄懎瀚夌€广儱鎳庨～?=================
echo -e "${YELLOW}[6/6] 闂佸憡鍑归崹鐗堟叏閳哄懎瀚夌€广儱鎳庨～?..${NC}"

# Python 缂備焦妫忛崹鎷屻亹濞戞娑㈠焵椤掑嫬钃?
check_port() {
    $VENV_PYTHON -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); exit(0 if s.connect_ex(('127.0.0.1', $1)) != 0 else 1)"
    return $?
}

for ((i=0; i<3; i++)); do
    # 闁诲繐绻戠换鍡涙儊閻ヮ櫙ll婵炲濮伴崕杈ㄦ櫠閻樿鐭楁い鏍ㄧ箓閸樺瓨绻涢崼婵愬剳闁哄棌鍋撻梺姹囧妼鐎氼剟骞冮幘璇茶Е鐎广儱妫欑粻娑㈡煕?(闂佸搫顑勭粈渚€宕ｉ崜褏涓嶉柍褜鍓熷畷锟犲即閻樺磭鏆犻梻鍌氬暞瑜板啴鎮ラ姀銏㈢煋妞ゆ牜鍋愰崑鎾诲及韫囨洖绔?
    # 濠电偛顦崝宥夊礈? 闁哄鏅滈悷鈺呭闯闁垮顩烽柛娑卞枟缁茬粯鎱ㄥ┑濠庡殭闁告鍥ㄥ殏闁哄倸鐏濋悡鍌滄喐閻楀牊灏褏濞€閹嫰顢欓懖鈺冃梺?uvicorn
    pkill -f "$PROJECT_DIR/backend/venv/bin/uvicorn" 2>/dev/null
done

DEFAULT_PORT=8000
PORT=$DEFAULT_PORT

for ((i=0; i<10; i++)); do
    if check_port $PORT; then
        echo -e "${GREEN}婵炶揪缍€濞夋洟寮妶鍥╁崥妞ゆ牗鑹剧紞? $PORT${NC}"
        break
    else
        echo "缂備焦妫忛崹鎷屻亹?$PORT 闁荤偞鍑归崑鍕暦娴煎瓨鍋ㄦい顓熷笧缁€澶愭倶韫囨梻绠氶柣?$((PORT+1))..."
        PORT=$((PORT+1))
    fi
done

cd "$BACKEND_DIR"

# 闂佸憡鍑归崹鎶藉极?Python 闂佸搫鍟版慨椋庢閿曞倸绀冮柛娆忣棧娴ｅ壊鍤曢煫鍥ュ劤缁€澶岀棯椤撗冩灆缂佺粯宀稿顕€濡烽妷褏顔嶉柣搴℃贡閸嬬偛顪冮崒鐐茬婵炲棗绻愬?
export PYTHONUNBUFFERED=1

# 闁哄鏅滈崝姗€銆侀幋锕€鏋侀柣妤€鐗嗙粊锕傚箹鐎涙ɑ灏€规洖鐬奸惀顏囶槺缂佹鐬肩划濠氭晬閸曨剙鈧偤鏌涘☉娆樼劸妞ゆ梹娲滅槐?
echo "闁圭厧鐡ㄥ濠氬极閵堝鏋侀柣妤€鐗嗙粊锕傚箹鐎涙ɑ灏憸鏉款樀瀵绱欓悩鐢垫喛缂備胶濯寸槐鏇㈠箖婵犲洤宸濇俊顖滅帛缂嶁偓闂?.."
UPDATE_ADMIN="$UPDATE_ADMIN" ADMIN_USER="$ADMIN_USER_VAL" ADMIN_PASS="$ADMIN_PASS_VAL" "$VENV_PYTHON" upgrade_admin.py

# 闂佹眹鍨婚崰宥嗩殽閸ヮ剚鍋濇い鏍ㄥ嚬閺嗘梻鈧偣鍊濈紓姘额敊閸涙潙鍌ㄧ紓浣姑粩?--reload闂佹寧绋戦懟顖灺烽崘顭戝殨闁惧繘顎囬弮鍌楀亾鐟欏嫮顣查柍?
echo "闂佸憡鍑归崹鐗堟叏閳哄懎瑙﹂幖杈剧秵娴煎倿鏌￠崼婵埿㈠┑?(Port: $PORT)..."
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port $PORT >> "$PROJECT_DIR/backend.log" 2>&1 &
PID=$!

sleep 5  # 婵犫拃鍛粶濠殿喚鍋熺划鍨緞婵犲嫮顎€闂佸搫鍟悥鐓幬涢崸妤佹櫖鐎光偓閳ь剟鍨惧Ο鑽も攳婵犻潧妫涢弳姘舵煕韫囧濡奸柟顖氶叄瀹曟繈濡搁敃鈧悘妤呮煙闊彃鍔﹂柡浣革躬閺屽懘鍩€椤掑嫬绀?
if ps -p $PID > /dev/null; then
    IP=$(hostname -I | awk '{print $1}')
    
    echo -e "${YELLOW}濠殿喗绻愮徊钘夛耿椤忓牆瑙︽い鏍ㄨ壘琚熼梺鍛婃尭缁夊爼顢旈鍕珘鐎广儱鎳庨～?(缂備焦妫忛崹鎷屻亹? $FRONTEND_PORT)...${NC}"
    
    # ----------------------------------------------------
    # 婵炶揪缍€濞夋洟寮?Node.js + Express 闂佸湱鈷堥崢鑲╃磽閹惧墎涓嶉柍褜鍓熷浼村箻閼稿灚娅㈡繛瀛樼眰閸涱厾鐣炬繝鈷€鍐ㄦ殨闁哥儐鍓熼幃鍫曞幢濡崵鐤€闂佸憡鏌￠埀顒冩珪閻?
    # 闁荤喐鐟辩徊浠嬪窗?serve 闂佸搫鍟版慨鐢垫兜閼哥數顩烽柨婵嗘处閸?/api 闁荤姴娲弨閬嶆儑閹殿喒鍋撴担鍐棈闁搞伇鍥ㄥ剭?404/undefined 闂傚倸鍋嗛崳锝夈€?
    # ----------------------------------------------------
    
    echo "闁诲海鎳撻ˇ鎶剿夋繝鍥ㄥ仺闁绘柨鐖奸悰鎾绘煟濠婂骸鐏犳い锝冨姂瀵潧顓奸崨顓ф匠婵炴挻纰嶇换鍡欑矉?(express, http-proxy-middleware)..."
    cd "$FRONTEND_DIR"
    npm install express http-proxy-middleware --no-save

    # 闂佹眹鍨婚崰鎰板垂?server.cjs (婵炶揪缍€濞夋洟寮?.cjs 闂備緡鍓欓悘婵嬪储?type: module 闂傚倸鍋嗛崳锝夈€?
    cat > server.cjs <<EOF
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const app = express();

const BACKEND_PORT = $PORT;
const FRONTEND_PORT = $FRONTEND_PORT;
const API_URL = "http://127.0.0.1:" + BACKEND_PORT;

console.log("闂佸憡鍑归崹鐗堟叏閳哄懎绀堢€广儱娴傛导鍌炴煛閸繄孝濠殿喚鍠栧畷?..");
console.log("婵炲濯寸徊鍧楀箖婵犲洦鍎庢い鏃傛櫕閸?", API_URL);

// 1. 闂備焦婢樼粔鍫曟偪?API 婵炲濯寸徊鍧楀箖?(婵?vite.config.ts 闂備緡鍋呭Σ鎺旀椤愶絿鈹嶆繝闈涙搐閻︻喖鈽夐幘顖氫壕闂?
app.use('/api', createProxyMiddleware({ 
    target: API_URL, 
    changeOrigin: true,
    xfwd: true, // Auto-add x-forwarded-for headers so backend sees real IP
    pathRewrite: { '^/api': '' },
    proxyTimeout: 600000, // 10闂佸憡甯掑Λ婵嬪箰閹捐埖鎯ラ柛娑卞枟椤ρ囨煥濞戞鐒告俊鑼额嚙椤?AI 闂佹眹鍨婚崰鎰板垂濮橆厽浜ら柛銉㈡杹閺屻倕鈽夐幙鍐ㄥ箺闁?
    timeout: 600000,      // 婵炵鍋愭慨鎾矗閸℃ɑ浜ら柣鎰綑婢跺秹鎮洪幒鎴炲櫤婵?
    onProxyReq: (proxyReq, req, res) => {
        // Keeps socket alive
        proxyReq.setTimeout(600000);
    },
    onError: (err, req, res) => {
        console.error('Proxy Error:', err);
        res.status(500).send('Proxy Error');
    }
}));

// 2. 闂佺懓鐏氶…鍥敇閹间焦顥堟繛鍡樺姀閸嬫挻鎷呯憴鍕偓顔济?(dist)
app.use(express.static(path.join(__dirname, 'dist')));

// 3. SPA 闂佹悶鍎抽崑銈夊焵椤戣棄浜?(闂佸湱顣介崑鎾绘煛閸繍妲搁柛娆戝亾缁傛帡寮介澶婃濠殿喚鎳撻崐鐣屾崲閹达箑鐐?index.html)
app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(FRONTEND_PORT, '0.0.0.0', () => {
  console.log(\`Frontend service running at http://0.0.0.0:\${FRONTEND_PORT}\`);
});
EOF

    # 濠电偞鎸搁幊鎰板箖婵犲洤绫嶇憸蹇撯枔閹达箑绀堢€广儱娴傛导鍌炲级閳哄倻銆掗柍?(婵犵鈧啿鈧綊鎮樻径鎰珘?
    fpid=$(lsof -t -i:$FRONTEND_PORT)
    if [ -n "$fpid" ]; then
        kill -9 $fpid
    fi
    
    # 闂佸憡鍑归崹鐗堟叏?Node 闂佸搫鐗嗙粔瀛樻叏?
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

    rm -f /usr/local/bin/miaobi
    install -m 755 "$PROJECT_DIR/miaobi" /usr/local/bin/miaobi 2>/dev/null || {
        cp -f "$PROJECT_DIR/miaobi" /usr/local/bin/miaobi
        chmod 755 /usr/local/bin/miaobi
    }
    
    echo -e "\n${GREEN}====== 闂備緡鍠撻崝搴ｆ媼閺屻儱绠ｉ柟閭﹀墮椤?======${NC}"
    echo -e "闂佸憡鎸哥粔鍫曨敂椤掑倹濯奸柛褎顨嗛敍鏍煕閿旇姤绶叉繛?  http://$IP:$FRONTEND_PORT"
    echo -e "闂佸憡鑹惧ù鐑筋敂?API 闂侀潻闄勫妯侯焽? http://$IP:$PORT"
    echo -e "--------------------------------------------------------"
    echo -e "闂佸憡鎸哥粔鍫曨敂椤掑嫬绫嶉柕澶堝劤缁?      tail -f $PROJECT_DIR/frontend.log"
    echo -e "闂佸憡鑹惧ù鐑筋敂椤掑嫬绫嶉柕澶堝劤缁?      tail -f $PROJECT_DIR/backend.log"
    echo -e "--------------------------------------------------------"
    echo -e "${YELLOW}闂備焦褰冪粔鐑姐€呴敃鍌氱闁归偊鍠撴禒? 闁荤姴娲ˉ鎾诲灳濡崵鈹嶆繝闈涙噽闂呮﹢鏌￠崼婵埿㈠┑顔惧枛瀹曟娊濡搁妷褎娈ч梺绋跨箞閸斿海鍒?闂傚倸鍟崇亸娆愬閳ь剙顭胯閻熴儵宕欓敓鐘茬哗闁硅鍔﹂弨鐣岀磼閺冩垵鐏犵憸? $PORT (闂佸憡鑹惧ù鐑筋敂? 闂?$FRONTEND_PORT (闂佸憡鎸哥粔鍫曨敂?${NC}"
else
    echo -e "${RED}闂佸憡鑹惧ù鐑筋敂椤掑嫬瑙︽い鏍ㄨ壘琚熸繝銏″劶缁墽鎲撮敃鍌涙櫖鐎光偓閸愭儳娈查梺鍝勮閸庡啿锕?backend.log${NC}"
    exit 1
fi
