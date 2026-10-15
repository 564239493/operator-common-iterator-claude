"""HTTP 层：读取及受限人工约束副本提交，路由 /api/* 与静态托管（SPA fallback）。

只读红线：本模块及下游 adapter 仅 review.save 可以原子写入人工约束副本。
"""
import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import jsonutil, paths
from .adapters import review
from .adapters import agents as agents_adapter
from .adapters import assets as assets_adapter
from .adapters import artifacts as artifacts_adapter
from .adapters import iterations as iter_adapter
from .adapters import replay as replay_adapter
from .adapters import run_detail, runs_index

_API_ROUTES = []  # (method, regex, handler)


def route(pattern):
    regex = re.compile("^" + pattern + "$")

    def decorator(func):
        _API_ROUTES.append((regex, func))
        return func

    return decorator


# ----------------------------- API handlers -----------------------------
# 每个 handler 签名：func(ctx, **path_params) -> data（dict/list）
# ctx: {"root": Path, "query": dict}

@route(r"/api/health")
def _health(ctx):
    return {"status": "up", "root": str(ctx["root"])}


@route(r"/api/runs")
def _runs(ctx):
    return runs_index.list_runs(ctx["root"])


@route(r"/api/agents")
def _agents(ctx):
    return agents_adapter.load_agent_defs(ctx["root"])


@route(r"/api/assets")
def _assets(ctx):
    return assets_adapter.load_assets(ctx["root"])


@route(r"/api/runs/(?P<run_id>[^/]+)")
def _run_view(ctx, run_id):
    return run_detail.build_run_view(ctx["root"], run_id)


@route(r"/api/runs/(?P<run_id>[^/]+)/replay")
def _replay(ctx, run_id):
    from .adapters import evidence as evidence_adapter
    ev = evidence_adapter.build_evidence(ctx["root"], run_id)
    return replay_adapter.build_replay(ev["run_state"], ev["iterations"], ev["inputs_files"])


@route(r"/api/runs/(?P<run_id>[^/]+)/iterations/(?P<n>\d+)")
def _iteration(ctx, run_id, n):
    run_root = paths.resolve_run(ctx["root"], run_id)
    iter_dir = paths.iter_dir_for(run_root, int(n))
    return iter_adapter.summarize_iteration(iter_dir, int(n))


@route(r"/api/runs/(?P<run_id>[^/]+)/iterations/(?P<n>\d+)/cases")
def _iteration_cases(ctx, run_id, n):
    run_root = paths.resolve_run(ctx["root"], run_id)
    iter_dir = paths.iter_dir_for(run_root, int(n))
    query = ctx["query"]
    offset = _int_param(query, "offset", 0)
    limit = _int_param(query, "limit", None)
    result_filter = _str_param(query, "result")
    return iter_adapter.page_records(iter_dir, offset=offset, limit=limit, result_filter=result_filter)


@route(r"/api/runs/(?P<run_id>[^/]+)/iterations/(?P<n>\d+)/logs/tail")
def _iteration_log_tail(ctx, run_id, n):
    run_root = paths.resolve_run(ctx["root"], run_id)
    paths.iter_dir_for(run_root, int(n))  # 校验轮次存在
    name = _str_param(ctx["query"], "name")
    if not name:
        raise artifacts_adapter.ArtifactRejected("缺少 name 参数")
    rel = "iter_%03d/%s" % (int(n), name)
    nbytes = _int_param(ctx["query"], "bytes", None)
    return artifacts_adapter.list_log_tail(run_root, rel, nbytes)


@route(r"/api/runs/(?P<run_id>[^/]+)/artifact")
def _artifact(ctx, run_id):
    run_root = paths.resolve_run(ctx["root"], run_id)
    rel = _str_param(ctx["query"], "path")
    if not rel:
        raise artifacts_adapter.ArtifactRejected("缺少 path 参数")
    return artifacts_adapter.get_artifact(run_root, rel)


def _int_param(query, key, default):
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


def _str_param(query, key):
    values = query.get(key)
    return values[0] if values else None


# ----------------------------- HTTP server -----------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "OpWorkbench/0.1"
    review_token = secrets.token_urlsafe(32)
    root = None      # 由 run_server 注入
    dist_dir = None  # 由 run_server 注入

    # 安静一点：访问日志走 stderr 一行
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # noqa: N802（标准库命名）
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/review/'):
            try:
                parts = [unquote(p) for p in path[len('/api/review/'):].split('/')]
                data = review.get(self.root, parts, parse_qs(parsed.query), self.review_token)
                self._send_json(200, data)
            except review.Conflict as exc:
                self._send_json(409, {'error': str(exc)})
            except paths.PathEscapeError as exc:
                self._send_json(403, {'error': str(exc)})
            except FileNotFoundError as exc:
                self._send_json(404, {'error': str(exc)})
            except (ValueError, OSError) as exc:
                self._send_json(400, {'error': str(exc)})
        elif path.startswith("/api/"):
            self._handle_api(path, parse_qs(parsed.query))
        else:
            self._handle_static(path)

    def do_POST(self):  # noqa: N802
        match = re.fullmatch(r'/api/review/runs/([^/]+)/(iter_\d+)/constraints_update', urlparse(self.path).path)
        if not match:
            self._send_json(405, {'error': '仅允许保存人工约束副本'})
            return
        origin = self.headers.get('Origin')
        if (origin and urlparse(origin).netloc != self.headers.get('Host')) or not secrets.compare_digest(self.headers.get('X-Review-Token', ''), self.review_token):
            self._send_json(403, {'error': '提交来源无效，请重新加载页面'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 8 * 1024 * 1024:
                raise ValueError('提交大小必须在 1 字节到 8 MB 之间')
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            result = review.save(self.root, unquote(match[1]), match[2], payload)
            self._send_json(200, result)
        except review.Conflict as exc:
            self._send_json(409, {'error': str(exc)})
        except paths.PathEscapeError as exc:
            self._send_json(403, {'error': str(exc)})
        except (ValueError, OSError) as exc:
            self._send_json(400, {'error': str(exc)})

    def do_PUT(self):
        self._send_json(405, {'error': '不支持该写入方法'})

    do_DELETE = do_PUT
    do_PATCH = do_PUT

    # ------------------------------ API ------------------------------
    def _handle_api(self, path, query):
        ctx = {"root": self.root, "query": query}
        for regex, func in _API_ROUTES:
            match = regex.match(path)
            if not match:
                continue
            try:
                data = func(ctx, **match.groupdict())
            except paths.PathEscapeError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
                return
            except artifacts_adapter.ArtifactRejected as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
                return
            except FileNotFoundError as exc:
                self._send_json(404, {"ok": False, "error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # 单端点异常不拖垮服务
                self._send_json(500, {"ok": False, "error": "内部错误: %s" % exc})
                return
            meta = {"generated_at": jsonutil.iso_from_ts(__import__("time").time())}
            self._send_json(200, {"ok": True, "data": data, "meta": meta})
            return
        self._send_json(404, {"ok": False, "error": "未知端点: %s" % path})

    # ----------------------------- static -----------------------------
    def _handle_static(self, path):
        if self.dist_dir is None or not self.dist_dir.is_dir():
            body = ("<html><body style='font-family:sans-serif;padding:40px'>"
                    "<h2>算子测试工作台</h2>"
                    "<p>前端尚未构建。请先执行：</p>"
                    "<pre>cd src/web && npm install --registry=https://registry.npmmirror.com && npm run build</pre>"
                    "<p>API 已可用：<a href='/api/runs'>/api/runs</a></p>"
                    "</body></html>").encode("utf-8")
            self._send_bytes(200, body, "text/html; charset=utf-8")
            return
        rel = path.lstrip("/") or "index.html"
        candidate = (self.dist_dir / rel).resolve()
        if not str(candidate).startswith(str(self.dist_dir.resolve())):
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        if not candidate.is_file():
            # SPA fallback
            candidate = self.dist_dir / "index.html"
            if not candidate.is_file():
                self._send_json(404, {"ok": False, "error": "not found"})
                return
        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        try:
            body = candidate.read_bytes()
        except OSError:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        self._send_bytes(200, body, ctype)

    # ----------------------------- helpers -----------------------------
    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run_server(host="127.0.0.1", port=8420, root=None):
    project = paths.project_root(root)
    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    handler = type("BoundHandler", (_Handler,), {"root": project, "dist_dir": dist})
    server = ThreadingHTTPServer((host, port), handler)
    print("算子测试工作台 · 可视化审核服务")
    print("  项目根: %s" % project)
    print("  地址:   http://%s:%d/" % (host, port))
    print("  前端:   %s" % ("已构建（%s）" % dist if dist.is_dir() else "未构建，仅 API 可用"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
