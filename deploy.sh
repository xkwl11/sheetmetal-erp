#!/bin/bash
# ============================================================
#  钣金ERP 一键部署脚本
#  适用：Ubuntu 20.04+ / Debian 11+
#  使用方法：bash deploy.sh
# ============================================================

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  钣金ERP 一键部署脚本 v1.0${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ---------- 1. 检测系统 ----------
echo -e "${YELLOW}[1/7] 检测系统环境...${NC}"

if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    VERSION=$VERSION_ID
else
    echo -e "${RED}无法识别操作系统，仅支持 Ubuntu/Debian${NC}"
    exit 1
fi

echo -e "${GREEN}✅ 系统: $OS $VERSION${NC}"

# ---------- 2. 检查 root 权限 ----------
if [ "$EUID" -ne 0 ]; then 
    echo -e "${YELLOW}⚠️  建议使用 root 用户或 sudo 执行${NC}"
    echo -e "${YELLOW}正在尝试使用 sudo...${NC}"
    exec sudo bash "$0" "$@"
    exit
fi

# ---------- 3. 安装 Python 环境 ----------
echo -e "${YELLOW}[2/7] 安装 Python 环境...${NC}"

if command -v python3 &> /dev/null; then
    echo -e "${GREEN}✅ Python 已安装: $(python3 --version)${NC}"
else
    apt update -qq
    apt install python3 python3-venv python3-pip -y
    echo -e "${GREEN}✅ Python 安装完成${NC}"
fi

# ---------- 4. 安装系统依赖 ----------
echo -e "${YELLOW}[3/7] 安装系统依赖...${NC}"

apt install -y libjpeg-dev zlib1g-dev libssl-dev

# ---------- 5. 创建项目目录 ----------
echo -e "${YELLOW}[4/7] 创建项目目录...${NC}"

PROJECT_DIR="/opt/sheetmetal-erp"
if [ -d "$PROJECT_DIR" ]; then
    echo -e "${YELLOW}⚠️  目录 $PROJECT_DIR 已存在${NC}"
    read -p "是否覆盖重新部署？(y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}部署已取消${NC}"
        exit 0
    fi
    rm -rf "$PROJECT_DIR"
fi

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
echo -e "${GREEN}✅ 目录创建完成: $PROJECT_DIR${NC}"

# ---------- 6. 上传/下载代码 ----------
echo -e "${YELLOW}[5/7] 获取代码...${NC}"

# 方法1：如果有git仓库，使用git clone
# git clone https://github.com/your-username/sheetmetal-erp.git .

# 方法2：从当前目录复制（如果在本地执行，需要先上传）
# 这里提示用户选择
echo -e "${YELLOW}请选择代码获取方式：${NC}"
echo "  1) 从本地上传（当前目录）"
echo "  2) 从 Git 仓库拉取"
echo "  3) 手动上传（稍后执行）"
read -p "请选择 (1/2/3): " choice

case $choice in
    1)
        # 如果脚本在本地执行，复制当前目录所有文件到项目目录
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        if [ "$SCRIPT_DIR" != "$PROJECT_DIR" ]; then
            cp -r "$SCRIPT_DIR"/* "$PROJECT_DIR/" 2>/dev/null || true
            cp -r "$SCRIPT_DIR"/.[!.]* "$PROJECT_DIR/" 2>/dev/null || true
            echo -e "${GREEN}✅ 已复制本地代码到 $PROJECT_DIR${NC}"
        else
            echo -e "${GREEN}✅ 已在项目目录中${NC}"
        fi
        ;;
    2)
        read -p "请输入 Git 仓库地址: " GIT_URL
        git clone "$GIT_URL" .
        echo -e "${GREEN}✅ Git 代码拉取完成${NC}"
        ;;
    3)
        echo -e "${YELLOW}请手动上传代码到 $PROJECT_DIR 目录${NC}"
        echo -e "${YELLOW}使用命令: scp -r ./ root@服务器IP:$PROJECT_DIR/${NC}"
        read -p "上传完成后按回车继续..."
        ;;
    *)
        echo -e "${RED}无效选择${NC}"
        exit 1
        ;;
esac

# ---------- 7. 检查必要文件 ----------
echo -e "${YELLOW}[6/7] 检查项目文件...${NC}"

if [ ! -f "$PROJECT_DIR/main.py" ]; then
    echo -e "${RED}❌ main.py 不存在，请确保代码已正确上传${NC}"
    exit 1
fi

# 创建 requirements.txt（如果不存在）
if [ ! -f "$PROJECT_DIR/requirements.txt" ]; then
    echo -e "${YELLOW}⚠️  未找到 requirements.txt，正在创建...${NC}"
    cat > "$PROJECT_DIR/requirements.txt" <<EOF
fastapi
uvicorn
jinja2
pydantic
Pillow
python-multipart
EOF
fi

# ---------- 8. 创建虚拟环境并安装依赖 ----------
echo -e "${YELLOW}[7/7] 创建虚拟环境并安装依赖...${NC}"
echo -e "${YELLOW}⏳ 安装过程可能需要 2-5 分钟，请耐心等待...${NC}"

cd "$PROJECT_DIR"

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境并安装依赖
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q
pip install Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple -q

echo -e "${GREEN}✅ 依赖安装完成${NC}"

# ---------- 9. 创建 systemd 服务 ----------
echo -e "${YELLOW}[额外] 创建 systemd 服务...${NC}"

cat > /etc/systemd/system/sheetmetal-erp.service <<EOF
[Unit]
Description=SheetMetal ERP Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ---------- 10. 开放防火墙 ----------
echo -e "${YELLOW}[额外] 开放 8000 端口...${NC}"

if command -v ufw &> /dev/null; then
    ufw allow 8000
    echo -e "${GREEN}✅ ufw 已放行 8000 端口${NC}"
else
    echo -e "${YELLOW}⚠️  ufw 未安装，请手动开放 8000 端口${NC}"
fi

# ---------- 11. 启动服务 ----------
echo -e "${YELLOW}[额外] 启动服务...${NC}"

systemctl start sheetmetal-erp
systemctl enable sheetmetal-erp

# 等待服务启动
sleep 3

if systemctl is-active --quiet sheetmetal-erp; then
    echo -e "${GREEN}✅ 服务已启动成功！${NC}"
else
    echo -e "${RED}❌ 服务启动失败，请查看日志:${NC}"
    echo -e "${YELLOW}sudo journalctl -u sheetmetal-erp -f${NC}"
    exit 1
fi

# ---------- 12. 显示部署信息 ----------
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}🎉 部署完成！${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "${GREEN}访问地址:${NC}"
echo -e "  http://$(curl -s ifconfig.me):8000"
echo -e "  http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo -e "${GREEN}常用命令:${NC}"
echo -e "  启动:  ${YELLOW}systemctl start sheetmetal-erp${NC}"
echo -e "  停止:  ${YELLOW}systemctl stop sheetmetal-erp${NC}"
echo -e "  重启:  ${YELLOW}systemctl restart sheetmetal-erp${NC}"
echo -e "  状态:  ${YELLOW}systemctl status sheetmetal-erp${NC}"
echo -e "  日志:  ${YELLOW}journalctl -u sheetmetal-erp -f${NC}"
echo ""
echo -e "${GREEN}项目目录:${NC} ${YELLOW}$PROJECT_DIR${NC}"
echo -e "${GREEN}数据库:${NC} ${YELLOW}$PROJECT_DIR/sheetmetal_erp.db${NC}"
echo -e "${BLUE}============================================${NC}"
