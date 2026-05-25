#!/usr/bin/env bash
# ============================================================================
# WildClawBench 本地化部署脚本
# 安装 OpenClaw + opik-openclaw 插件 + Opik Server (Docker)
# ============================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}==== $* ====${NC}"; }

# ============================================================================
# 检查前置依赖
# ============================================================================
log_step "Step 1: 检查前置依赖"

check_command() {
    if ! command -v "$1" &>/dev/null; then
        log_error "$1 未安装。$2"
        exit 1
    fi
    log_info "$1 ✓ ($(command -v "$1"))"
}

check_command "node" "请安装 Node.js >= 22.12.0: https://nodejs.org/"
check_command "npm" "请安装 npm >= 10"
check_command "docker" "请安装 Docker: https://docs.docker.com/get-docker/"
check_command "python3" "请安装 Python >= 3.10"

# 检查 Node 版本
NODE_MAJOR=$(node --version | cut -d'.' -f1 | tr -d 'v')
if [ "$NODE_MAJOR" -lt 22 ]; then
    log_error "Node.js 版本过低 ($(node --version))，需要 >= 22.12.0"
    exit 1
fi
log_info "Node.js 版本: $(node --version) ✓"

# ============================================================================
# 安装 OpenClaw
# ============================================================================
log_step "Step 2: 安装 OpenClaw"

if command -v openclaw &>/dev/null; then
    CURRENT_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
    log_info "OpenClaw 已安装: $CURRENT_VERSION"
    read -rp "是否更新到最新版本? [y/N] " update_choice
    if [[ "$update_choice" =~ ^[Yy]$ ]]; then
        npm install -g @anthropic-ai/openclaw@latest
        log_info "OpenClaw 已更新到最新版本"
    fi
else
    log_info "正在安装 OpenClaw..."
    npm install -g @anthropic-ai/openclaw@latest
    log_info "OpenClaw 安装完成: $(openclaw --version)"
fi

# ============================================================================
# 安装 opik-openclaw 插件
# ============================================================================
log_step "Step 3: 安装 opik-openclaw 插件"

# 检查插件是否已安装
if openclaw plugins list 2>/dev/null | grep -q "opik-openclaw"; then
    log_info "opik-openclaw 插件已安装"
else
    log_info "正在安装 opik-openclaw 插件..."
    openclaw plugins install clawhub:@opik/opik-openclaw
    log_info "opik-openclaw 插件安装完成"
fi

# ============================================================================
# 部署 Opik Server (本地 Docker)
# ============================================================================
log_step "Step 4: 部署 Opik Server"

OPIK_RUNNING=$(docker ps --filter "name=opik-server" --format '{{.Names}}' 2>/dev/null || true)

if [ -n "$OPIK_RUNNING" ]; then
    log_info "Opik Server 已在运行"
else
    read -rp "选择 Opik 部署方式: [1] 本地 Docker (推荐) [2] 使用 comet.com 云端: " opik_choice
    
    if [[ "$opik_choice" == "2" ]]; then
        log_info "使用 comet.com 云端 Opik"
        log_info "请确保设置环境变量: OPIK_API_KEY, OPIK_URL_OVERRIDE=https://www.comet.com/opik/api"
    else
        log_info "正在启动本地 Opik Server..."
        
        # 检查是否有 opik docker-compose
        OPIK_DIR="$HOME/.opik"
        mkdir -p "$OPIK_DIR"
        
        if [ ! -f "$OPIK_DIR/docker-compose.yml" ]; then
            log_info "下载 Opik docker-compose 配置..."
            curl -sSL https://raw.githubusercontent.com/comet-ml/opik/main/deployment/docker-compose/docker-compose.yml \
                -o "$OPIK_DIR/docker-compose.yml"
        fi
        
        cd "$OPIK_DIR"
        docker compose up -d
        
        log_info "等待 Opik Server 启动..."
        sleep 10
        
        # 验证 Opik Server 启动
        MAX_RETRIES=30
        for i in $(seq 1 $MAX_RETRIES); do
            if curl -s http://localhost:5173/api/v1/private/traces?limit=1 >/dev/null 2>&1; then
                log_info "Opik Server 启动成功! 访问: http://localhost:5173"
                break
            fi
            if [ "$i" -eq "$MAX_RETRIES" ]; then
                log_warn "Opik Server 可能还在启动中，请稍后手动验证: http://localhost:5173"
            fi
            sleep 2
        done
        
        cd - >/dev/null
    fi
fi

# ============================================================================
# 配置 opik-openclaw 插件
# ============================================================================
log_step "Step 5: 配置 opik-openclaw 插件"

OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
mkdir -p "$(dirname "$OPENCLAW_CONFIG")"

# 读取或设置 Opik API URL
OPIK_API_URL="${OPIK_URL_OVERRIDE:-http://localhost:5173/api}"
OPIK_KEY="${OPIK_API_KEY:-}"
OPIK_PROJECT="${OPIK_PROJECT_NAME:-wildclaw-bench}"

log_info "Opik API URL: $OPIK_API_URL"
log_info "Opik Project: $OPIK_PROJECT"

# 写入插件配置到 openclaw.json
python3 - <<'PY'
import json
import os
from pathlib import Path

config_path = Path.home() / ".openclaw" / "openclaw.json"
config = {}
if config_path.exists():
    config = json.loads(config_path.read_text())

plugins = config.setdefault("plugins", {})
entries = plugins.setdefault("entries", {})
allow_list = plugins.setdefault("allow", [])

if "opik-openclaw" not in allow_list:
    allow_list.append("opik-openclaw")

opik_entry = entries.setdefault("opik-openclaw", {})
opik_entry["enabled"] = True
opik_entry.setdefault("hooks", {})["allowConversationAccess"] = True

opik_config = opik_entry.setdefault("config", {})
opik_config["enabled"] = True
opik_config["apiUrl"] = os.environ.get("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
opik_config["projectName"] = os.environ.get("OPIK_PROJECT_NAME", "wildclaw-bench")
opik_config.setdefault("workspaceName", "default")
opik_config.setdefault("tags", ["wildclaw-bench"])
opik_config["staleTraceCleanupEnabled"] = True
opik_config["staleTraceTimeoutMs"] = 300000
opik_config["staleSweepIntervalMs"] = 60000
opik_config["flushRetryCount"] = 2
opik_config["flushRetryBaseDelayMs"] = 250

api_key = os.environ.get("OPIK_API_KEY", "")
if api_key:
    opik_config["apiKey"] = api_key

config_path.write_text(json.dumps(config, indent=2))
print(f"✓ 插件配置已写入: {config_path}")
PY

# ============================================================================
# 安装 Python 依赖
# ============================================================================
log_step "Step 6: 安装 Python 依赖"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    pip install -r "$PROJECT_ROOT/requirements.txt" --quiet
    log_info "基础依赖安装完成"
fi

# 安装本地模式额外依赖
pip install requests --quiet
log_info "额外依赖安装完成"

# ============================================================================
# 配置环境变量
# ============================================================================
log_step "Step 7: 配置环境变量"

ENV_FILE="$PROJECT_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$PROJECT_ROOT/.env.example" ]; then
        cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
        log_info "已从 .env.example 创建 .env 文件"
    fi
fi

log_warn "请确保在 .env 文件中配置以下关键变量:"
echo "  OPENROUTER_API_KEY=your-key-here"
echo "  OPIK_API_KEY=your-opik-key (如果使用云端 Opik)"
echo "  OPIK_URL_OVERRIDE=http://localhost:5173/api (本地) 或 https://www.comet.com/opik/api (云端)"
echo "  OPIK_PROJECT_NAME=wildclaw-bench"

# ============================================================================
# 验证安装
# ============================================================================
log_step "Step 8: 验证安装"

echo ""
log_info "=== 安装验证 ==="
echo -n "  OpenClaw:        "; openclaw --version 2>/dev/null || echo "未找到"
echo -n "  Node.js:         "; node --version
echo -n "  npm:             "; npm --version
echo -n "  Python:          "; python3 --version
echo -n "  Docker:          "; docker --version | head -1

echo ""
log_info "opik-openclaw 插件状态:"
openclaw plugins list 2>/dev/null | grep -i opik || log_warn "插件可能需要重启后生效"

# ============================================================================
# 完成
# ============================================================================
log_step "安装完成! 🎉"

echo ""
echo "快速开始:"
echo "  # 运行单个任务（本地模式）"
echo "  bash script/run.sh openclaw-local --task tasks/01_Productivity_Flow/task_1.md --model openrouter/anthropic/claude-sonnet-4.6"
echo ""
echo "  # 运行整个类别"
echo "  bash script/run.sh openclaw-local --category 01_Productivity_Flow --parallel 2"
echo ""
echo "  # 查看 Opik 监控面板"
echo "  open http://localhost:5173"
echo ""
echo "  # 检查 opik 插件状态"
echo "  openclaw opik status"
echo ""
