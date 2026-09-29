"""安全读 JSON、ISO 时间处理、绝对路径重映射。"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def read_json(path, max_bytes=None):
    # type: (Path, int) -> object
    """读 JSON 文件；任何失败返回 {"_error": msg}，绝不抛异常拖垮页面。"""
    limit = max_bytes or config.MAX_JSON_READ_BYTES
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"_error": "stat 失败: %s" % exc}
    if size > limit:
        return {"_error": "文件过大（%d 字节 > 上限 %d）" % (size, limit)}
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"_error": "JSON 解析失败: %s" % exc}


def is_error(obj):
    return isinstance(obj, dict) and "_error" in obj


def remap_paths(obj, root):
    # type: (object, Path) -> object
    """递归遍历，把以项目根开头的绝对路径字符串重映射为 ~/ 相对形式。"""
    prefix = str(root.resolve())
    return _remap(obj, prefix)


def _remap(obj, prefix):
    if isinstance(obj, str):
        if obj.startswith(prefix + "/"):
            return "~/" + obj[len(prefix) + 1:]
        if obj == prefix:
            return "~"
        return obj
    if isinstance(obj, list):
        return [_remap(item, prefix) for item in obj]
    if isinstance(obj, dict):
        return {key: _remap(value, prefix) for key, value in obj.items()}
    return obj


def parse_iso(text):
    # type: (str) -> object
    """解析带时区 ISO 时间；失败返回 None。"""
    if not text or not isinstance(text, str):
        return None
    try:
        normalized = text.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def iso_from_ts(ts):
    # type: (float) -> str
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def mtime_iso(path):
    # type: (Path) -> object
    try:
        return iso_from_ts(path.stat().st_mtime)
    except OSError:
        return None


def max_iso(*values):
    # type: (str) -> object
    """返回若干 ISO 字符串中最大的那个（None 忽略）；全 None 返回 None。"""
    parsed = [(v, parse_iso(v)) for v in values if v]
    parsed = [(v, dt) for v, dt in parsed if dt is not None]
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[1])[0]
