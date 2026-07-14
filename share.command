#!/bin/zsh

set -e

cd -- "$(dirname -- "$0")"

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "未找到 cloudflared，请先运行：brew install cloudflared"
    exit 1
fi

if ! command -v waitress-serve >/dev/null 2>&1; then
    echo "未找到 waitress，请先运行：python3 -m pip install -r requirements.txt"
    exit 1
fi

if [[ -f .env ]]; then
    chmod 600 .env
fi

APP_PID=""
TUNNEL_PID=""
LOG_FILE="/tmp/rehab_chatbot_flask.log"
TUNNEL_LOG="/tmp/rehab_chatbot_tunnel.log"
# 每次使用新文件名，避免 macOS“预览”缓存并继续显示上一次的失效二维码。
QR_FILE="/tmp/rehab_chatbot_share_qr_$$.png"
ACCESS_TOKEN=$(openssl rand -hex 16)

cleanup() {
    if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" >/dev/null 2>&1; then
        kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    fi
    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" >/dev/null 2>&1; then
        kill "$APP_PID" >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT INT TERM

if curl -fsS http://127.0.0.1:5030/healthz >/dev/null 2>&1; then
    echo "5030 端口已有服务运行。请先在原终端按 Control+C 关闭，再重新运行本脚本。"
    exit 1
fi

echo "正在启动康复机器人……"
# 分享模式只监听本机，由 Cloudflare Tunnel 提供唯一外部入口。
SHARE_MODE=1 SHARE_ACCESS_TOKEN="$ACCESS_TOKEN" FLASK_DEBUG=0 PYTHONUNBUFFERED=1 \
    waitress-serve --listen=127.0.0.1:5030 --threads=16 --connection-limit=100 \
    --channel-timeout=30 --max-request-header-size=16384 --max-request-body-size=8192 \
    app:app \
    >"$LOG_FILE" 2>&1 &
APP_PID=$!

READY=0
for _ in {1..30}; do
    if curl -fsS http://127.0.0.1:5030/healthz >/dev/null 2>&1; then
        READY=1
        break
    fi

    if ! kill -0 "$APP_PID" >/dev/null 2>&1; then
        echo "机器人启动失败，错误日志如下："
        tail -n 30 "$LOG_FILE"
        exit 1
    fi

    sleep 0.2
done

if [[ "$READY" != "1" ]]; then
    echo "机器人启动超时，请查看日志：$LOG_FILE"
    exit 1
fi

echo ""
echo "正在生成 HTTPS 分享链接……"
rm -f "$TUNNEL_LOG"

cloudflared tunnel --url http://127.0.0.1:5030 >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

SHARE_URL=""
for _ in {1..60}; do
    SHARE_URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)
    if [[ -n "$SHARE_URL" ]]; then
        break
    fi

    if ! kill -0 "$TUNNEL_PID" >/dev/null 2>&1; then
        echo "HTTPS 分享通道启动失败，错误日志如下："
        tail -n 30 "$TUNNEL_LOG"
        exit 1
    fi

    sleep 0.5
done

if [[ -z "$SHARE_URL" ]]; then
    echo "获取分享链接超时，请查看日志：$TUNNEL_LOG"
    exit 1
fi

DISPLAY_URL="$SHARE_URL/?access=$ACCESS_TOKEN"

echo ""
echo "============================================================"
echo "手机体验链接：$DISPLAY_URL"
echo "============================================================"
echo ""

if command -v qrencode >/dev/null 2>&1; then
    qrencode -o "$QR_FILE" -s 12 -m 3 "$DISPLAY_URL"
    echo "二维码已经生成并打开，现场可直接扫码。"
    echo "二维码文件：$QR_FILE"
    open "$QR_FILE"
else
    echo "未找到 qrencode，只显示分享链接。可运行：brew install qrencode"
fi

echo "分享期间请保持电脑联网，不要关闭本窗口。"
echo "按 Control+C 可以结束分享，链接会随即失效。"
echo ""

wait "$TUNNEL_PID"
