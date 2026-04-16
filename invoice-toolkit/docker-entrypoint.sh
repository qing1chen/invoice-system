#!/bin/bash
# ============================================================
#  docker-entrypoint.sh — 容器启动脚本
#
#  放置位置：invoice-toolkit/ 项目根目录（与 Dockerfile.backend 同级）
#
#  启动顺序：
#  1. Xvfb   — 虚拟 X 显示服务（DISPLAY=:99）
#  2. x11vnc — VNC 服务器，连接到 Xvfb
#  3. noVNC  — Web 前端，将 VNC 暴露为 http://localhost:6080
#  4. 主应用 — 传入的 CMD
#
#  用户通过 http://localhost:6080 即可在浏览器中看到容器内的
#  Playwright Chromium 有头窗口，手动完成登录等操作。
# ============================================================

set -e

echo "═══════════════════════════════════════════════════════"
echo "  🚀 启动容器环境"
echo "═══════════════════════════════════════════════════════"

# ── 1. 启动 Xvfb（虚拟 X 显示）──
DISPLAY_NUM="${DISPLAY:-:99}"
DISPLAY_NUM="${DISPLAY_NUM#:}"  # 去掉冒号前缀

echo "  📺 启动 Xvfb (DISPLAY=:${DISPLAY_NUM})..."
Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1

# 验证 Xvfb 是否正常运行
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "  ❌ Xvfb 启动失败！"
    exit 1
fi
echo "  ✅ Xvfb 已启动 (PID: $XVFB_PID)"

export DISPLAY=:${DISPLAY_NUM}

# ── 2. 启动 x11vnc（VNC 服务器）──
echo "  📡 启动 x11vnc..."
x11vnc -display :${DISPLAY_NUM} -forever -nopw -shared -rfbport 5900 -bg -o /tmp/x11vnc.log 2>/dev/null
sleep 0.5
echo "  ✅ x11vnc 已启动 (端口: 5900)"

# ── 3. 启动 noVNC（Web VNC 客户端）──
if [ -d "/opt/noVNC" ]; then
    echo "  🌐 启动 noVNC (端口: 6080)..."
    /opt/noVNC/utils/novnc_proxy --vnc localhost:5900 --listen 6080 &
    NOVNC_PID=$!
    sleep 1
    echo "  ✅ noVNC 已启动"
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║  📺 浏览器查看地址: http://localhost:6080       ║"
    echo "  ║  点击 Connect 即可看到容器内的浏览器窗口          ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo ""
else
    echo "  ⚠️  noVNC 未安装，跳过 (VNC 仍可通过 5900 端口访问)"
fi

# ── 4. 优雅退出处理 ──
cleanup() {
    echo ""
    echo "  🛑 正在关闭服务..."
    [ -n "$NOVNC_PID" ] && kill $NOVNC_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    pkill x11vnc 2>/dev/null || true
    echo "  ✅ 已清理"
}
trap cleanup EXIT SIGTERM SIGINT

echo "═══════════════════════════════════════════════════════"
echo "  🏃 启动主应用: $@"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── 5. 启动主应用（透传 CMD 参数）──
exec "$@"
