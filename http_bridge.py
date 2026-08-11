#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《忒修斯之脑》HTTP 桥 —— 让接不了 stdio 的客户端也能玩（手机 App 之类）。

    python3 http_bridge.py                    # 只听本机，和桌面共用同一条世系
    python3 http_bridge.py --host 0.0.0.0 --isolate --token 你自己想一个
                                              # 架在公网上给别人玩

端点 `/mcp`（POST）。零依赖，只用标准库。

─────────────────────────────────────────────────────────────────────────────
为什么不 subprocess

外面有人写过一版：起一个子进程跑 `server.py`，把 HTTP 收到的 JSON 喂给它的
标准输入，再读回一行。思路对，但会死锁 —— MCP 客户端握手完必发一条
`notifications/initialized`，**这条消息不带 id，按协议不该有回复**，
`serve_stdio` 也确实不回它。子进程方案却无条件去读那一行，于是永远等下去，
一条握手消息就把整座桥卡死。

这一版**不开子进程**：直接 import server，调它的 `handle_request`。
管道那一整类毛病连同死锁一起没有了。不带 id 的通知照协议回 202，不回正文。

─────────────────────────────────────────────────────────────────────────────
存档：默认共用，公网必须隔离

引擎的存档路径是模块级全局（`SAVE_DIR` / `LEGACY_PATH` / `CURRENT_PATH`），
一个进程一份。所以：

  默认（--shared）  所有连进来的人共用 saves/ —— **这正是自己在家用时想要的**：
                    手机接上来，玩的就是桌面那条世系。
  --isolate         每个 MCP 会话一份 saves/http/<会话 id>/。
                    **架在公网上必须开这个** —— 否则陌生人换过的身体会出现在
                    你的下一世里。（这游戏的档案就是它的全部意义。）

切换靠的是每次请求前把那三个全局换成本会话的值，和 `--selftest` 里那套做法同源。
引擎有全局状态，所以所有调用都在一把锁里排队。
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import server  # noqa: E402  —— 必须在 sys.path 补好之后

MCP_PATH = "/mcp"
MAX_BODY = 1 << 20          # 1 MiB。游戏的输入都是短的，再大只可能是恶意或事故
MAX_SESSIONS = 256          # 超了就把最久没露面的那个请出去
SESSION_HEADER = "Mcp-Session-Id"

_LOCK = threading.Lock()    # 引擎是全局状态，所有调用在这里排队
_SESSIONS = {}              # sid -> {"game":…, "dir":…, "seen":…}
CONF = {"isolate": False, "token": None, "allow_origin": False}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

def _current_name():
    """跟引擎自己的命名保持一致：中英两种语言的进行中存档分开放。"""
    return "current.json" if server.LANG == "zh" else "current.%s.json" % server.LANG


def _session(sid):
    """取（或开）一个会话。不隔离时全都指向同一个引擎和同一份存档。"""
    if not CONF["isolate"]:
        sid = "*shared*"
    s = _SESSIONS.get(sid)
    if s is None:
        if CONF["isolate"]:
            d = os.path.join(BASE_DIR, "saves", "http", sid)
            os.makedirs(d, exist_ok=True)
        else:
            d = server.SAVE_DIR
        _bind(d)
        s = {"game": server.Game(), "dir": d, "seen": time.time()}
        _SESSIONS[sid] = s
        _evict()
    s["seen"] = time.time()
    return s


def _bind(d):
    """把引擎的三个存档全局指到 d。调用点必须已经拿着 _LOCK。"""
    server.SAVE_DIR = d
    server.LEGACY_PATH = os.path.join(d, "legacy.json")
    server.CURRENT_PATH = os.path.join(d, _current_name())


def _evict():
    """会话上限。踢掉的只是内存里那个引擎对象 —— **存档留在盘上**，
    那个人再连回来，`Game()` 会把没走完的一世原样捞回去。"""
    while len(_SESSIONS) > MAX_SESSIONS:
        oldest = min(_SESSIONS, key=lambda k: _SESSIONS[k]["seen"])
        _SESSIONS.pop(oldest, None)


def dispatch(msg, sid):
    """一条 JSON-RPC 请求进，一条回应出。整段在锁里，因为引擎是全局的。"""
    with _LOCK:
        s = _session(sid)
        _bind(s["dir"])
        server.GAME = s["game"]
        try:
            result = server.handle_request(msg)
        except Exception as e:              # 引擎炸了也不该炸掉整座桥
            return {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": "internal error: %r" % e}}
    if result is None:
        return {"jsonrpc": "2.0", "id": msg.get("id"),
                "error": {"code": -32601,
                          "message": "Method not found: %s" % msg.get("method")}}
    return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "theseus-brain-bridge/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 出口 ----
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   extra=extra)

    def _err(self, code, message, rpc_id=None):
        self._json(code, {"jsonrpc": "2.0", "id": rpc_id,
                          "error": {"code": -32600, "message": message}})

    # ---- 门口的两道检查 ----
    def _origin_ok(self):
        """浏览器发得出 Origin，手机 App 一般不发。挡的是 DNS rebinding：
        别人诱导你的浏览器去打你本机这个端口。MCP 规范点名要求做这一条。"""
        if CONF["allow_origin"]:
            return True
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return any(origin.startswith(p) for p in
                   ("http://localhost", "http://127.0.0.1",
                    "https://localhost", "https://127.0.0.1"))

    def _token_ok(self):
        if not CONF["token"]:
            return True
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() == CONF["token"]
        return self.headers.get("X-Auth-Token") == CONF["token"]

    # ---- 方法 ----
    def do_GET(self):
        # 根路径回一句人话：托管平台拿它做健康检查，人拿它确认「活着没」。
        if self.path.rstrip("/") in ("", "/"):
            self._send(200, b"theseus-brain MCP bridge is running. POST /mcp\n",
                       ctype="text/plain; charset=utf-8")
            return
        # /mcp 的 GET 是留给「服务器主动推」的 SSE 流。我们不推，照规范回 405。
        self._json(405, {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32000,
                                   "message": "This bridge does not offer an SSE stream."}},
                   extra={"Allow": "POST, DELETE"})

    def do_DELETE(self):
        sid = self.headers.get(SESSION_HEADER)
        with _LOCK:
            _SESSIONS.pop(sid, None)
        self._send(204)

    def do_POST(self):
        if not self._origin_ok():
            self._err(403, "Origin not allowed."); return
        if not self._token_ok():
            self._err(401, "Bad or missing token."); return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._err(400, "Bad Content-Length."); return
        if length > MAX_BODY:
            self._err(413, "Body too large."); return
        try:
            msg = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._json(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error: %s" % e}})
            return

        sid = self.headers.get(SESSION_HEADER)
        issued = None
        batch = isinstance(msg, list)
        items = msg if batch else [msg]
        if not all(isinstance(m, dict) for m in items):
            self._err(400, "Each message must be a JSON object."); return

        # initialize 时发一个会话 id 回去。**这是隔离存档的凭据**，
        # 客户端之后每一次请求都该带着它。不带也能玩，只是拿不到自己那一份。
        if any(m.get("method") == "initialize" for m in items) and not sid:
            sid = issued = uuid.uuid4().hex

        # 不带 id 的是通知（notifications/initialized 就是），照 JSON-RPC 不回正文。
        # **上一版桥就死在这里**：它无条件等一行回应，而这一条永远不会有。
        out = [dispatch(m, sid) for m in items if "id" in m]
        for m in items:
            if "id" not in m:
                dispatch_notification(m, sid)

        extra = {SESSION_HEADER: sid} if (issued or sid) else None
        if not out:
            self._send(202, extra=extra)
            return
        self._json(200, out if batch else out[0], extra=extra)

    def log_message(self, fmt, *args):      # 默认那套访问日志太吵
        pass


def dispatch_notification(msg, sid):
    """通知不回正文，但该让引擎知道的还是要送进去（现在只有握手那一条，
    引擎不认识它 —— 不认识也没关系，重点是**不能在这儿等回话**）。"""
    try:
        with _LOCK:
            s = _session(sid)
            _bind(s["dir"])
            server.GAME = s["game"]
            server.handle_request(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 自测：拿真的 HTTP 打自己
# ---------------------------------------------------------------------------

def run_selftest():
    import tempfile
    import urllib.error
    import urllib.request

    global BASE_DIR
    # 自测整个搬进临时目录 —— **绝不许碰真的 saves/**。
    # （第一版这里漏了：BASE_DIR 没换，隔离档直接写进了仓库里那份存档目录。）
    real_base, root = BASE_DIR, tempfile.mkdtemp(prefix="theseus-bridge-test-")
    BASE_DIR = root
    server.SAVE_DIR = os.path.join(root, "shared")
    os.makedirs(server.SAVE_DIR, exist_ok=True)
    server.REQUIRE_MODE = False             # 自测没有人类可以问菜单
    CONF["isolate"] = True
    CONF["token"] = "s3cret"

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % port

    def post(obj, sid=None, token="s3cret", timeout=10, path=MCP_PATH):
        data = json.dumps(obj).encode()
        req = urllib.request.Request(url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        if sid:
            req.add_header(SESSION_HEADER, sid)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return r.status, r.headers.get(SESSION_HEADER), (json.loads(body) if body else None)

    # ① 握手：拿得到会话 id
    st, sid, res = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert st == 200 and res["result"]["serverInfo"]["name"] == "theseus-brain", "握手失败"
    assert sid, "initialize 没有发回会话 id"

    # ② **上一版桥死在这一条上。** 不带 id 的通知：202、空正文、而且不许卡。
    t0 = time.time()
    st, _, res = post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, timeout=8)
    assert st == 202 and res is None, "通知应当回 202 且没有正文，实得 %s" % st
    assert time.time() - t0 < 5, "通知把桥卡住了 —— 这正是要防的那个死锁"

    # ③ 卡过之后还能不能用（死锁的真正代价是后面全废）
    st, _, res = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
    names = [t["name"] for t in res["result"]["tools"]]
    assert "new_run" in names and "status" in names, "工具表不对：%s" % names

    # ④ 真玩一手：开局 → 选 → 断线重连拿回那一幕
    st, _, res = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "new_run",
                                  "arguments": {"seed": 4242, "mode": "story"}}}, sid)
    assert "忒 修 斯 之 脑" in res["result"]["content"][0]["text"], "没开出局"
    post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
          "params": {"name": "choose", "arguments": {"option": 1}}}, sid)
    st, _, res = post({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "new_run", "arguments": {}}}, sid)
    assert "这一世还没走完" in res["result"]["content"][0]["text"], \
        "隔着 HTTP，new_run 把进行中的一世盖掉了"

    # ⑤ 隔离：另一个会话是另一条世系，互相看不见
    st, sid2, res = post({"jsonrpc": "2.0", "id": 6, "method": "initialize", "params": {}})
    assert sid2 and sid2 != sid, "第二个会话没拿到自己的 id"
    st, _, res = post({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "status", "arguments": {}}}, sid2)
    assert "尚未开局" in res["result"]["content"][0]["text"], \
        "两个人的存档串了 —— 陌生人的身体出现在了另一个人的世系里"
    assert os.path.isdir(os.path.join(BASE_DIR, "saves", "http", sid)), "隔离目录没建起来"

    # ⑥ 会话 id 是凭据：换一个人拿着别人的 id 才接得上（这里只验「不带 id 也不炸」）
    st, sid3, res = post({"jsonrpc": "2.0", "id": 8, "method": "initialize", "params": {}})
    assert sid3 not in (sid, sid2), "会话 id 撞车了"

    # ⑦ 门口两道检查
    try:
        post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, sid, token="wrong")
        raise AssertionError("令牌错了竟然放行")
    except urllib.error.HTTPError as e:
        assert e.code == 401, "令牌错了该回 401，实得 %d" % e.code
    req = urllib.request.Request(url + MCP_PATH, data=b'{"jsonrpc":"2.0","id":1}',
                                 headers={"Content-Type": "application/json",
                                          "Origin": "https://evil.example",
                                          "Authorization": "Bearer s3cret"})
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)
        raise AssertionError("外来 Origin 竟然放行")
    except urllib.error.HTTPError as e:
        assert e.code == 403, "外来 Origin 该回 403，实得 %d" % e.code

    # ⑧ 坏 JSON 不炸桥
    req = urllib.request.Request(url + MCP_PATH, data=b"{ not json",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer s3cret"})
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)
        raise AssertionError("坏 JSON 竟然当好的收了")
    except urllib.error.HTTPError as e:
        assert e.code == 400, "坏 JSON 该回 400，实得 %d" % e.code

    # ⑨ GET：根路径是健康检查，/mcp 照规范回 405
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + "/", timeout=10) as r:
        assert b"running" in r.read(), "健康检查没回话"
    try:
        opener.open(url + MCP_PATH, timeout=10)
        raise AssertionError("/mcp 的 GET 该回 405")
    except urllib.error.HTTPError as e:
        assert e.code == 405, "/mcp 的 GET 该回 405，实得 %d" % e.code

    # ⑩ DELETE 之后那个会话从内存里走了，但**存档还在盘上**
    req = urllib.request.Request(url + MCP_PATH, method="DELETE")
    req.add_header(SESSION_HEADER, sid2)
    opener.open(req, timeout=10)
    assert sid2 not in _SESSIONS, "DELETE 没能结束会话"
    assert os.path.isdir(os.path.join(BASE_DIR, "saves", "http", sid2)), \
        "会话结束把人家的存档也删了"

    httpd.shutdown()
    # ⑪ 自测本身不许留下痕迹：真的那份 saves/ 一个新目录都不该多出来
    assert not os.path.exists(os.path.join(real_base, "saves", "http")), \
        "自测把测试存档写进了真的 saves/ 里"
    BASE_DIR = real_base
    print("HTTP 桥自测通过（握手/通知不死锁/卡过还能用/隔着 HTTP 也盖不掉进行中的一世/"
          "会话隔离/令牌/Origin/坏 JSON/健康检查与 405/DELETE 不删档/自测不碰真存档）。")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="《忒修斯之脑》HTTP 桥：给接不了 stdio 的客户端用")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址。默认只听本机；架给别人玩才改 0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT") or 8787),
                    help="端口（默认 8787，或环境变量 PORT）")
    ap.add_argument("--isolate", action="store_true",
                    help="每个会话一份存档。**架在公网上必须开**")
    ap.add_argument("--token", default=os.environ.get("THESEUS_TOKEN"),
                    help="访问令牌，客户端用 Authorization: Bearer 带上")
    ap.add_argument("--allow-origin", action="store_true",
                    help="放行任意 Origin（默认只放行本机，防 DNS rebinding）")
    ap.add_argument("--selftest", action="store_true", help="自测")
    args = ap.parse_args()

    if args.selftest:
        run_selftest()
        return

    CONF.update(isolate=args.isolate, token=args.token,
                allow_origin=args.allow_origin)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("《忒修斯之脑》HTTP 桥：http://%s:%d%s" % (args.host, args.port, MCP_PATH))
    print("  存档：%s" % ("每个会话一份（saves/http/<会话 id>/）" if args.isolate
                         else "共用一份（saves/）—— 和桌面同一条世系"))
    print("  令牌：%s" % ("已设" if args.token else "无"))
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        if not args.isolate:
            print("  ⚠ 对外监听却没开 --isolate：所有人共用同一条世系。")
        if not args.token:
            print("  ⚠ 对外监听却没设 --token：谁找到地址谁就能玩你的档。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n收摊。进行中的一世已经落盘，下次连上来接着走。")


if __name__ == "__main__":
    main()
