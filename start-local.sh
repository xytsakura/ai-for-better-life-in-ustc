#!/usr/bin/env bash
# 本地（非 Docker）启动脚本：Campus Agent Hub + 瀚海行 course-agent + 校园助手 demo-agent
# 端口映射（全部 127.0.0.1）：
#   Hub        8100
#   course-agent 8002 （对外公开，SSE/chat 也走 8002）
#   demo-agent    8101
#
# 启动顺序（关键）：
#   1. Hub
#   2. register-only bootstrap：提交两 Agent Manifest、生成 Featured credential、写 secret 文件
#      （此时 agent 还没起，先做注册，让 course-agent 启动能读到 secret）
#   3. 启动 course-agent（init-db + import + uvicorn）与 demo-agent
#   4. 完整 bootstrap：等两 agent 就绪后跑 conformance 与审核
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[start-local] 已从 .env.example 创建 .env"
fi
source "$ROOT/.env"

HUB_PUBLIC_PORT="${HUB_PUBLIC_PORT:-8100}"
COURSE_AGENT_PUBLIC_PORT="${COURSE_AGENT_PUBLIC_PORT:-8002}"
DEMO_AGENT_PUBLIC_PORT="${DEMO_AGENT_PUBLIC_PORT:-8101}"

# 运行时数据/凭据目录（替代 compose volume）
RUNTIME_DIR="$ROOT/var"
HUB_DATA="$RUNTIME_DIR/hub"
COURSE_DATA="$RUNTIME_DIR/course-agent"
SECRETS_DIR="$RUNTIME_DIR/hub-secrets"
mkdir -p "$HUB_DATA" "$COURSE_DATA" "$SECRETS_DIR"

COURSE_SECRET_FILE="$SECRETS_DIR/course-agent.secret"

# 统一 URL（本地全部用 127.0.0.1，替换 compose 里的容器名）
HUB_URL="http://127.0.0.1:${HUB_PUBLIC_PORT:-8100}"
COURSE_PUBLIC="http://127.0.0.1:${COURSE_AGENT_PUBLIC_PORT:-8002}"
COURSE_INTERNAL="$COURSE_PUBLIC"   # 本地单进程，内外部同地址
DEMO_PUBLIC="http://127.0.0.1:${DEMO_AGENT_PUBLIC_PORT:-8101}"
DEMO_INTERNAL="$DEMO_PUBLIC"

log() { echo "[start-local] $*"; }

# 停止之前可能残留的进程
pkill -f "uvicorn hub.main:app" 2>/dev/null || true
pkill -f "uvicorn course_agent.main:app" 2>/dev/null || true
pkill -f "demo_agent.main run" 2>/dev/null || true
pkill -f "bootstrap_demo.py" 2>/dev/null || true
sleep 1

# ---------- 1. Hub ----------
log "启动 Hub @ $HUB_PUBLIC_PORT"
HUB_DEMO_MODE="${HUB_DEMO_MODE:-true}" \
HUB_HOST=127.0.0.1 \
HUB_PORT="${HUB_PUBLIC_PORT:-8100}" \
HUB_DATABASE_PATH="$HUB_DATA/hub.sqlite3" \
HUB_PUBLIC_BASE_URL="$HUB_URL" \
HUB_INTERNAL_URL_ALLOWLIST="$COURSE_INTERNAL,$DEMO_INTERNAL" \
HUB_CORS_ALLOW_ORIGINS="$HUB_URL" \
PYTHONPATH="$ROOT/apps/hub" \
  "$ROOT/apps/hub/.venv/bin/python" -m uvicorn hub.main:app \
  --host 127.0.0.1 --port "${HUB_PUBLIC_PORT:-8100}" \
  > "$RUNTIME_DIR/hub.log" 2>&1 &
HUB_PID=$!

# 等待 Hub 就绪
for _ in $(seq 1 60); do
  if curl -fs "$HUB_URL/api/session" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fs "$HUB_URL/api/session" >/dev/null 2>&1 || { log "Hub 未就绪，查看 $RUNTIME_DIR/hub.log"; exit 1; }
log "Hub 已就绪"

# ---------- 2. register-only bootstrap（生成 secret，供 course-agent 读取） ----------
log "注册两 Agent 并生成 Featured credential（register-only）"
CONTRACT_ROOT="$ROOT/contracts/campus-agent-hub/v1" \
HUB_URL="$HUB_URL" \
HUB_ADMIN_USER=demo-a \
COURSE_AGENT_PUBLIC_URL="$COURSE_PUBLIC" \
COURSE_AGENT_INTERNAL_URL="$COURSE_INTERNAL" \
DEMO_AGENT_PUBLIC_URL="$DEMO_PUBLIC" \
DEMO_AGENT_INTERNAL_URL="$DEMO_INTERNAL" \
COURSE_AGENT_SECRET_PATH="$COURSE_SECRET_FILE" \
HUB_BOOTSTRAP_REGISTER_ONLY=1 \
  "$ROOT/apps/hub/.venv/bin/python" "$ROOT/deploy/bootstrap_demo.py"
log "secret 已写入 $COURSE_SECRET_FILE"

# ---------- 3. course-agent ----------
log "初始化并导入 course-agent 资料"
cd "$ROOT/apps/course-agent"
PYTHONPATH="$ROOT/apps/course-agent" \
  "$ROOT/apps/course-agent/.venv/bin/python" -m course_agent.cli init-db
PYTHONPATH="$ROOT/apps/course-agent" \
  "$ROOT/apps/course-agent/.venv/bin/python" -m course_agent.cli import-manifest \
  "$ROOT/data/manifests/math-analysis-b1.yaml"

log "启动 course-agent @ $COURSE_AGENT_PUBLIC_PORT"
COURSE_AGENT_DEMO_MODE=true \
COURSE_AGENT_SESSION_SECRET="${COURSE_AGENT_SESSION_SECRET:-local-course-session-change-before-shared}" \
COURSE_AGENT_SESSION_HTTPS_ONLY=false \
COURSE_AGENT_RUNTIME_DIR="$COURSE_DATA" \
COURSE_AGENT_ADMIN_USER_IDS=demo-a \
COURSE_AGENT_LLM_API_KEY="${COURSE_AGENT_LLM_API_KEY:-}" \
COURSE_AGENT_LLM_BASE_URL="${COURSE_AGENT_LLM_BASE_URL:-}" \
COURSE_AGENT_LLM_MODEL="${COURSE_AGENT_LLM_MODEL:-gpt-5.6-sol}" \
COURSE_AGENT_LLM_API_STYLE="${COURSE_AGENT_LLM_API_STYLE:-responses}" \
COURSE_AGENT_BRANCH_LLM_MODEL="${COURSE_AGENT_BRANCH_LLM_MODEL:-gpt-5.6-sol}" \
COURSE_AGENT_LLM_MAX_OUTPUT_TOKENS="${COURSE_AGENT_LLM_MAX_OUTPUT_TOKENS:-1200}" \
COURSE_AGENT_HUB_AGENT_ID=hanhai-course-agent \
COURSE_AGENT_HUB_CONTRACT_VERSION=1.0 \
COURSE_AGENT_HUB_ISSUER=campus-agent-hub \
COURSE_AGENT_HUB_JWKS_URL="$HUB_URL/.well-known/jwks.json" \
COURSE_AGENT_HUB_AUTH_REQUIRED=true \
COURSE_AGENT_HUB_TOKEN_ENDPOINT="$HUB_URL/oauth/token" \
COURSE_AGENT_HUB_CLIENT_ID=hanhai-course-agent \
COURSE_AGENT_HUB_CLIENT_SECRET_FILE="$COURSE_SECRET_FILE" \
COURSE_AGENT_HUB_WORKSPACE_REDIRECT_URI="$COURSE_PUBLIC/api/hub/callback" \
COURSE_AGENT_HUB_RETURN_URL="$HUB_URL/" \
COURSE_AGENT_HUB_USER_MAP=demo-a:demo-a,demo-b:demo-b,demo-c:demo-c \
PYTHONPATH="$ROOT/apps/course-agent" \
  "$ROOT/apps/course-agent/.venv/bin/python" -m uvicorn course_agent.main:app \
  --host 127.0.0.1 --port "${COURSE_AGENT_PUBLIC_PORT:-8002}" \
  > "$RUNTIME_DIR/course-agent.log" 2>&1 &
COURSE_PID=$!

# ---------- 4. demo-agent ----------
log "启动 demo-agent @ $DEMO_AGENT_PUBLIC_PORT"
DEMO_AGENT_HOST=127.0.0.1 \
DEMO_AGENT_PORT="${DEMO_AGENT_PUBLIC_PORT:-8101}" \
DEMO_AGENT_REQUIRE_HUB_TOKEN=1 \
DEMO_AGENT_HUB_JWKS_URL="$HUB_URL/.well-known/jwks.json" \
DEMO_AGENT_HUB_AUDIENCE=campus-helper-demo \
DEMO_AGENT_HUB_ISSUER=campus-agent-hub \
PYTHONPATH="$ROOT/apps/demo-agent" \
  "$ROOT/apps/demo-agent/.venv/bin/python" -m demo_agent.main run \
  > "$RUNTIME_DIR/demo-agent.log" 2>&1 &
DEMO_PID=$!

# 等待两 agent 就绪
for _ in $(seq 1 60); do
  c_ok=$(curl -fs "$COURSE_PUBLIC/api/health" >/dev/null 2>&1 && echo 1 || echo 0)
  d_ok=$(curl -fs "$DEMO_PUBLIC/api/health" >/dev/null 2>&1 && echo 1 || echo 0)
  if [ "$c_ok" = 1 ] && [ "$d_ok" = 1 ]; then break; fi
  sleep 1
done
curl -fs "$COURSE_PUBLIC/api/health" >/dev/null 2>&1 && log "course-agent health OK" || log "course-agent health 未就绪（见 $RUNTIME_DIR/course-agent.log）"
curl -fs "$DEMO_PUBLIC/api/health" >/dev/null 2>&1 && log "demo-agent health OK" || log "demo-agent health 未就绪（见 $RUNTIME_DIR/demo-agent.log）"

# ---------- 5. 完整 bootstrap（conformance + 审核 + health） ----------
log "运行完整 bootstrap（conformance + 审核）"
CONTRACT_ROOT="$ROOT/contracts/campus-agent-hub/v1" \
HUB_URL="$HUB_URL" \
HUB_ADMIN_USER=demo-a \
COURSE_AGENT_PUBLIC_URL="$COURSE_PUBLIC" \
COURSE_AGENT_INTERNAL_URL="$COURSE_INTERNAL" \
DEMO_AGENT_PUBLIC_URL="$DEMO_PUBLIC" \
DEMO_AGENT_INTERNAL_URL="$DEMO_INTERNAL" \
COURSE_AGENT_SECRET_PATH="$COURSE_SECRET_FILE" \
  "$ROOT/apps/hub/.venv/bin/python" "$ROOT/deploy/bootstrap_demo.py"

log "全部服务已后台启动："
log "  Hub          -> $HUB_URL"
log "  course-agent -> $COURSE_PUBLIC"
log "  demo-agent   -> $DEMO_PUBLIC"
log "进程 PID: hub=$HUB_PID course=$COURSE_PID demo=$DEMO_PID"
log "日志目录: $RUNTIME_DIR"
log "停止：pkill -f 'uvicorn hub.main:app'; pkill -f 'uvicorn course_agent.main:app'; pkill -f 'demo_agent.main'"
log "可通过 $HUB_URL 打开 Campus Agent Hub 应用广场。"

wait
