import os
import hmac
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from agent import RehabChatAgent

SHARE_MODE = os.environ.get("SHARE_MODE") == "1"
SHARE_ACCESS_TOKEN = os.environ.get("SHARE_ACCESS_TOKEN", "")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=8 * 1024,
    MAX_FORM_MEMORY_SIZE=8 * 1024,
    MAX_FORM_PARTS=20,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=4),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SHARE_MODE,
)
if SHARE_MODE:
    app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1", ".trycloudflare.com"]

# 每台手机通过签名 Cookie 获取独立的训练 Agent，避免多人体验时状态串台。
# 会话只保存在内存中，服务重启后自动清空。
AGENT_SESSION_TTL = 2 * 60 * 60
MAX_AGENT_SESSIONS = 500
PER_SESSION_REQUEST_LIMIT = 30
GLOBAL_REQUEST_LIMIT = 240
RATE_LIMIT_WINDOW = 60
_agent_sessions = {}
_agent_sessions_lock = threading.Lock()
_global_request_times = deque()


def _prune_agent_sessions(now):
    expired_ids = [
        session_id
        for session_id, entry in _agent_sessions.items()
        if now - entry["last_seen"] > AGENT_SESSION_TTL
    ]
    for session_id in expired_ids:
        _agent_sessions.pop(session_id, None)

    overflow = len(_agent_sessions) - MAX_AGENT_SESSIONS
    if overflow > 0:
        oldest = sorted(
            _agent_sessions.items(),
            key=lambda item: item[1]["last_seen"],
        )
        for session_id, _ in oldest[:overflow]:
            _agent_sessions.pop(session_id, None)


def _current_agent_entry():
    session_id = session.get("rehab_session_id")
    now = time.monotonic()

    with _agent_sessions_lock:
        _prune_agent_sessions(now)
        entry = _agent_sessions.get(session_id) if session_id else None

        if entry is None:
            if len(_agent_sessions) >= MAX_AGENT_SESSIONS:
                oldest_session_id = min(
                    _agent_sessions,
                    key=lambda key: _agent_sessions[key]["last_seen"],
                )
                _agent_sessions.pop(oldest_session_id, None)

            session_id = uuid.uuid4().hex
            session["rehab_session_id"] = session_id
            entry = {
                "agent": RehabChatAgent(),
                "lock": threading.Lock(),
                "last_seen": now,
                "request_times": deque(),
            }
            _agent_sessions[session_id] = entry
        else:
            entry["last_seen"] = now

    return entry


def _consume_request_slot(entry):
    """限制单个体验者及全场的请求频率，避免接口和模型额度被刷。"""
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW

    with _agent_sessions_lock:
        while _global_request_times and _global_request_times[0] < cutoff:
            _global_request_times.popleft()
        if len(_global_request_times) >= GLOBAL_REQUEST_LIMIT:
            return False

        request_times = entry["request_times"]
        while request_times and request_times[0] < cutoff:
            request_times.popleft()
        if len(request_times) >= PER_SESSION_REQUEST_LIMIT:
            return False

        _global_request_times.append(now)
        request_times.append(now)
        return True


@app.before_request
def protect_shared_demo():
    """分享模式必须通过本次二维码中的随机令牌进入。"""
    if not SHARE_MODE or request.endpoint == "healthz":
        return None

    if session.get("share_authorized"):
        return None

    candidate = request.args.get("access", "")
    if SHARE_ACCESS_TOKEN and hmac.compare_digest(candidate, SHARE_ACCESS_TOKEN):
        session.clear()
        session["share_authorized"] = True
        session.permanent = True
        return redirect(url_for("index"))

    return "此分享入口无效或已过期，请扫描本次展示的新二维码。", 403


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "microphone=(self), camera=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Cache-Control"] = "no-store"
    if SHARE_MODE:
        response.headers["Strict-Transport-Security"] = "max-age=86400"
    return response


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "输入内容过长，请缩短后再试。"}), 413


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/")
def index():
    """
    手机或电脑访问首页时，显示 index.html。
    """
    return render_template("index.html")


@app.route("/start", methods=["GET"])
def start():
    """
    页面刚打开时调用。
    让机器人主动问候用户。
    """
    entry = _current_agent_entry()
    with entry["lock"]:
        if not _consume_request_slot(entry):
            return jsonify({"error": "请求过于频繁，请稍后再试。"}), 429
        result = entry["agent"].welcome()
    return jsonify(result)


@app.route("/chat", methods=["POST"])
def chat():
    """
    手机网页每次发送文字或按钮指令，都会进入这里。
    然后交给 agent.py 处理。
    """
    data = request.get_json(silent=True) or {}
    user_text = str(data.get("text") or "")
    if len(user_text) > 500:
        return jsonify({"error": "单次输入不能超过 500 个字符。"}), 400

    entry = _current_agent_entry()
    with entry["lock"]:
        if not _consume_request_slot(entry):
            return jsonify({"error": "请求过于频繁，请稍后再试。"}), 429
        result = entry["agent"].handle_input(user_text)
    return jsonify(result)


if __name__ == "__main__":
    """
    host='0.0.0.0' 表示允许局域网内手机访问。
    port=5030 避免和 Mac 的 AirPlay / 其他服务占用 5000 冲突。
    """
    ssl_context = "adhoc" if os.environ.get("HTTPS") == "1" else None
    app.run(
        # 默认只允许本机访问；需要局域网调试时才显式设置 APP_HOST=0.0.0.0。
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=5030,
        # 公开分享时不要开启 Flask 调试器；本地调试可设置 FLASK_DEBUG=1。
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
        threaded=True,
        ssl_context=ssl_context,
    )
