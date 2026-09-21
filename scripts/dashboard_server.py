#!/usr/bin/env python3
"""算子结果展示平台 - 轻量 HTTP server.

仅使用 Python 标准库，读取 runs/ 目录提供 JSON API。
用法:
    python scripts/dashboard_server.py
    # 浏览器打开 http://localhost:8899/static/operator-result.html
"""

from __future__ import annotations
import hashlib
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

PORT = 8899
ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
STATIC_DIR = ROOT / "static"
COVER_DIR = ROOT / "operator_cover_doc"

# 安全: run_id / artifact 名只允许字母数字下划线连字符
_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# 状态机阶段顺序 (用于排序, 非校验)
STATE_ORDER = [
    "PLAN",
    "INITIAL_EXTRACT",
    "EXTRACT",
    "SUPPLEMENT",
    "CONSTRAINT_CHECK",
    "GENERATE",
    "EXECUTE",
    "GATE",
    "DIAGNOSE",
    "UPDATE_CONSTRAINTS",
    "MIXED_FAILURE_REVIEW",
    "HUMAN_CHECKPOINT",
    "SUCCESS",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
    "STOPPED_BY_USER",
    "BLOCKED",
]

ALLOWED_ARTIFACTS = {
    "constraints",
    "constraint_check",
    "cases",
    "execution_result",
    "quality_gate",
    "generation_summary",
    "generate_result",
    "analysis",
    "operator_doc",
}

def _send_json(handler: BaseHTTPRequestHandler, data, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_text(handler: BaseHTTPRequestHandler, text: str, status: int = 200,
               content_type: str = "text/plain; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_file(handler: BaseHTTPRequestHandler, path: Path,
               content_type: str = None) -> None:
    if not path.is_file():
        _send_text(handler, "Not Found", 404)
        return
    if content_type is None:
        guessed, _ = mimetypes.guess_type(str(path))
        content_type = guessed or "application/octet-stream"
        # 文本类补 charset
        if content_type.startswith("text/") or content_type in (
                "application/javascript", "application/json", "image/svg+xml"):
            content_type += "; charset=utf-8"
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def _list_runs():
    """扫描 runs/ 目录, 返回所有 run 的摘要信息."""
    runs = []
    if not RUNS_DIR.is_dir():
        return runs
    # 与 runs/ 目录顺序保持一致 (自然目录序)
    for entry in sorted(RUNS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        state_file = entry / "run_state.json"
        rs = _load_json(state_file)
        if rs is None:
            # 没有 run_state.json 的目录跳过
            continue
        # 从 run_id 或目录名提取算子名
        run_id = rs.get("run_id", entry.name)
        operator = run_id.rsplit("-", 3)[0] if "-" in run_id else run_id
        # 当前状态: 直接取 state 字段 (state 与 history 是两个独立字段)
        runs.append({
            "run_id": run_id,
            "dir_name": entry.name,
            "operator": operator,
            "state": rs.get("state", "UNKNOWN"),
            "mode": rs.get("mode", ""),
            "test_framework": rs.get("test_framework", ""),
            "operator_family": rs.get("operator_family", ""),
            "current_iteration": rs.get("current_iteration", 0),
            "created_at": rs.get("created_at", ""),
        })
    return runs

def _list_cover_dirs():
    """扫描 operator_cover_doc/ 目录, 返回每个覆盖率数据目录的摘要."""
    items = []
    if not COVER_DIR.is_dir():
        return items
    for entry in sorted(COVER_DIR.iterdir()):
        if not entry.is_dir():
            continue
        jsons = list(entry.glob("*_coverage.json"))
        if not jsons:
            continue
        operator = entry.name
        # 从 coverage.json 的 operator 字段取更准确的算子名
        data = _load_json(jsons[0])
        if data and isinstance(data.get("operator"), str):
            operator = data["operator"]
        items.append({
            "dir_name": entry.name,
            "operator": operator,
            "has_coverage": True,
            "has_analysis": (entry / "analysis.md").is_file(),
        })
    return items

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path)
        # 剥离 query string (?run=...&iter=...) 后再匹配路由
        base_path = path.split("?", 1)[0]

        # 根路径: 重定向到算子结果展示平台 (保留 query string 以便深链定位任务/轮次)
        if base_path == "/":
            qs = path[len(base_path):]  # 含开头的 "?", 无 query 时为空串
            self.send_response(302)
            self.send_header("Location", "/static/operator-result.html" + qs)
            self.end_headers()
            return

        # /cover → 覆盖率展示页面 (友好路由, 重定向到 static/cover/cover.html)
        if base_path == "/cover":
            self.send_response(302)
            self.send_header("Location", "/static/cover/cover.html")
            self.end_headers()
            return

        # /static/<path>: 项目 static/ 目录下的页面与静态资源 (支持子目录, 防路径穿越)
        m = re.match(r"^/static/(.+)$", base_path)
        if m:
            target = (STATIC_DIR / m.group(1)).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                # 解析后逃出 static/ 目录 (含 .. 穿越尝试) → 拒绝
                _send_text(self, "Forbidden", 403)
                return
            _send_file(self, target)
            return

        if base_path == "/api/runs":
            _send_json(self, _list_runs())
            return

        # /api/cover/dirs — operator_cover_doc 下所有覆盖率数据目录
        if base_path == "/api/cover/dirs":
            _send_json(self, _list_cover_dirs())
            return

        # /api/cover/<dir>/coverage — 该目录下的 *_coverage.json
        m = re.match(r"^/api/cover/([A-Za-z0-9_\-]+)/coverage$", base_path)
        if m:
            dir_name = m.group(1)
            d = COVER_DIR / dir_name
            if not d.is_dir():
                _send_json(self, {"error": "cover dir not found"}, 404)
                return
            jsons = sorted(d.glob("*_coverage.json"))
            if not jsons:
                _send_json(self, {"error": "no *_coverage.json in dir"}, 404)
                return
            data = _load_json(jsons[0])
            if data is None:
                _send_json(self, {"error": "coverage json parse error"}, 500)
                return
            _send_json(self, data)
            return

        # /api/cover/<dir>/analysis — 该目录下的 analysis.md 原文
        m = re.match(r"^/api/cover/([A-Za-z0-9_\-]+)/analysis$", base_path)
        if m:
            dir_name = m.group(1)
            d = COVER_DIR / dir_name
            if not d.is_dir():
                _send_json(self, {"error": "cover dir not found"}, 404)
                return
            md = d / "analysis.md"
            if not md.is_file():
                _send_json(self, {"error": "analysis.md not found"}, 404)
                return
            try:
                content = md.read_text(encoding="utf-8")
            except OSError as e:
                _send_json(self, {"error": f"read error: {e}"}, 500)
                return
            _send_json(self, {"filename": md.name, "content": content})
            return

        # /api/runs/<run_id>
        m = re.match(r"^/api/runs/([^/]+)$", base_path)
        if m:
            run_id = m.group(1)
            if not _SAFE_RE.match(run_id):
                _send_json(self, {"error": "invalid run_id"}, 400)
                return
            # 在 runs/ 目录中查找匹配的 run (用 dir_name 匹配更安全)
            run_dir = RUNS_DIR / run_id
            if not run_dir.is_dir():
                _send_json(self, {"error": "run not found"}, 404)
                return
            rs = _load_json(run_dir / "run_state.json")
            if rs is None:
                _send_json(self, {"error": "run_state.json missing"}, 404)
                return
            _send_json(self, rs)
            return

        # /api/runs/<run_id>/iters — 任务目录下 iter_ 开头的迭代文件夹名
        m = re.match(r"^/api/runs/([^/]+)/iters$", base_path)
        if m:
            run_id = m.group(1)
            if not _SAFE_RE.match(run_id):
                _send_json(self, {"error": "invalid run_id"}, 400)
                return
            run_dir = RUNS_DIR / run_id
            if not run_dir.is_dir():
                _send_json(self, {"error": "run not found"}, 404)
                return
            dirs = sorted(
                e.name for e in run_dir.iterdir()
                if e.is_dir() and e.name.startswith("iter_")
            )
            _send_json(self, {"run_id": run_id, "dirs": dirs})
            return

        # /api/runs/<run_id>/operator_doc — 算子文档原文 (路径取 run_state.json 的 operator_doc 字段)
        m = re.match(r"^/api/runs/([^/]+)/operator_doc$", base_path)
        if m:
            run_id = m.group(1)
            if not _SAFE_RE.match(run_id):
                _send_json(self, {"error": "invalid run_id"}, 400)
                return
            run_dir = RUNS_DIR / run_id
            if not run_dir.is_dir():
                _send_json(self, {"error": "run not found"}, 404)
                return
            rs = _load_json(run_dir / "run_state.json")
            if rs is None:
                _send_json(self, {"error": "run_state.json missing"}, 404)
                return
            doc_path_str = rs.get("operator_doc", "")
            if not doc_path_str:
                _send_json(self, {"error": "operator_doc field missing in run_state.json"}, 404)
                return
            doc_path = Path(doc_path_str)
            if not doc_path.is_file():
                # 回退: 原路径不存在时, 尝试 run 目录下 inputs/<同名文件>
                fallback = run_dir / "inputs" / doc_path.name
                if fallback.is_file():
                    doc_path = fallback
                else:
                    _send_json(self, {"error": "operator_doc file not found",
                                      "path": doc_path_str}, 404)
                    return
            doc = doc_path.read_text(encoding="utf-8")
            _send_json(self, {"filename": doc_path.name, "path": str(doc_path), "content": doc})
            return

        # /api/runs/<run_id>/<iter_dir>/<artifact>  (iter_dir 形如 iter_001)
        m = re.match(r"^/api/runs/([^/]+)/(iter_\d+)/([A-Za-z0-9_\-]+)$", base_path)
        if m:
            run_id, iter_dir, artifact = m.group(1), m.group(2), m.group(3)
            if not _SAFE_RE.match(run_id):
                _send_json(self, {"error": "invalid run_id"}, 400)
                return
            if artifact not in ALLOWED_ARTIFACTS:
                _send_json(self, {"error": f"artifact '{artifact}' not allowed"}, 400)
                return
            run_dir = RUNS_DIR / run_id
            if not run_dir.is_dir():
                _send_json(self, {"error": "run not found"}, 404)
                return
            artifact_path = run_dir / iter_dir / f"{artifact}.json"
            data = _load_json(artifact_path)
            if data is None:
                _send_json(self, {"error": "artifact not found",
                                  "path": str(artifact_path.relative_to(ROOT))}, 404)
                return
            _send_json(self, data)
            return

        _send_text(self, "Not Found", 404)

    def do_POST(self):
        path = unquote(self.path)
        base_path = path.split("?", 1)[0]

        # /api/runs/<run_id>/<iter_dir>/constraints_update
        m = re.match(r"^/api/runs/([^/]+)/(iter_\d+)/constraints_update$", base_path)
        if m:
            run_id, iter_dir = m.group(1), m.group(2)
            if not _SAFE_RE.match(run_id):
                _send_json(self, {"error": "invalid run_id"}, 400)
                return
            run_dir = RUNS_DIR / run_id
            if not run_dir.is_dir():
                _send_json(self, {"error": "run not found"}, 404)
                return

            # 读取完整 constraints.json
            src_path = run_dir / iter_dir / "constraints.json"
            if not src_path.is_file():
                _send_json(self, {"error": "constraints.json not found",
                                  "path": str(src_path.relative_to(ROOT))}, 404)
                return
            constraints = _load_json(src_path)
            if constraints is None:
                _send_json(self, {"error": "constraints.json parse error"}, 500)
                return

            # 读取请求体
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                req = json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, ValueError, OSError):
                _send_json(self, {"error": "invalid request body"}, 400)
                return

            new_constraints = req.get("constraints")
            if not isinstance(new_constraints, dict):
                _send_json(self, {"error": "missing or invalid 'constraints' object"}, 400)
                return

            # 合并: 用编辑后的 constraints_in_parameters 替换原值
            cnp = new_constraints.get("constraints_in_parameters")
            if cnp is None:
                _send_json(self, {"error": "missing 'constraints_in_parameters'"}, 400)
                return
            merged = dict(constraints)
            merged["constraints_in_parameters"] = cnp

            # 删除每条约束条目的 status 字段 (运行态字段, 不入落盘产物)
            def _strip_status(cnp_obj):
                if isinstance(cnp_obj, dict):
                    # dict-by-product: {product: [条目, ...]}
                    for items in cnp_obj.values():
                        if isinstance(items, list):
                            for it in items:
                                if isinstance(it, dict):
                                    it.pop("status", None)
                elif isinstance(cnp_obj, list):
                    # array: [条目, ...]
                    for it in cnp_obj:
                        if isinstance(it, dict):
                            it.pop("status", None)

            _strip_status(merged["constraints_in_parameters"])

            out_path = run_dir / iter_dir / "constraints_copy.json"
            out_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8"
            )

            _send_json(self, {
                "ok": True,
                "path": str(out_path.relative_to(ROOT)),
                "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
            })
            return

        _send_json(self, {"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        # 静默默认日志, 只打印错误
        if args and "404" in str(args[1]):
            super().log_message(fmt, *args)

def main():
    print(f"算子结果展示平台服务启动中...")
    print(f"  项目根目录: {ROOT}")
    print(f"  runs 目录:  {RUNS_DIR}")
    print(f"  端口:       {PORT}")
    if not RUNS_DIR.is_dir():
        print(f"  [警告] runs/ 目录不存在, 请先执行一次算子测试")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"\n  浏览器打开: http://localhost:{PORT}  (自动重定向到算子结果展示平台)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()