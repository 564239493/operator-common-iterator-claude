"""原始产物查看端点：白名单 + realpath 防逃逸 + 大小截断。"""
import hashlib
import json

from .. import config, jsonutil, paths


class ArtifactRejected(ValueError):
    """产物请求被白名单拒绝。"""


def _basename_allowed(name):
    if name in config.ARTIFACT_BASENAMES:
        return True
    return any(name.startswith(prefix) and name.endswith(".json")
               for prefix in config.ARTIFACT_BASENAME_PREFIXES)


def _truncate_json(obj):
    """对超大 JSON 做结构截断：顶层长列表替换为计数 + 头部样本。"""
    if not isinstance(obj, dict):
        return obj, False
    truncated = False
    out = {}
    for key, value in obj.items():
        if isinstance(value, list) and len(value) > 20:
            out[key] = {
                "_truncated": True,
                "count": len(value),
                "head": value[:3],
                "_note": "完整内容请直接查看 run 目录下原文件",
            }
            truncated = True
        elif isinstance(value, str) and len(value) > 20000:
            out[key] = value[:20000] + "\n…（字符串截断，共 %d 字符）" % len(value)
            truncated = True
        else:
            out[key] = value
    return out, truncated


def get_artifact(run_root, rel_path):
    # type: (...) -> dict
    """读取 run 根内的 JSON 产物，返回 {json, size, sha256, truncated, path}。"""
    pure = rel_path.replace("\\", "/")
    name = pure.rsplit("/", 1)[-1]
    if not name.endswith(".json"):
        raise ArtifactRejected("仅允许查看 .json 产物：%r" % rel_path)
    if not _basename_allowed(name):
        raise ArtifactRejected("不在产物白名单内：%r" % name)

    path = paths.resolve_within(run_root, rel_path)  # 逃逸/不存在在此抛错
    size = path.stat().st_size
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    raw = jsonutil.read_json(path)
    truncated = False
    if size > config.ARTIFACT_TRUNCATE_BYTES and not jsonutil.is_error(raw):
        raw, truncated = _truncate_json(raw)
    return {
        "path": pure,
        "size": size,
        "sha256": sha256,
        "truncated": truncated,
        "json": raw,
        "json_text": json.dumps(raw, ensure_ascii=False, indent=2)[:400000],
    }


def list_log_tail(run_root, rel_path, nbytes=None):
    # type: (...) -> dict
    from ..parsers import logtail

    pure = rel_path.replace("\\", "/")
    name = pure.rsplit("/", 1)[-1]
    if name not in config.LOG_BASENAMES:
        raise ArtifactRejected("不在日志白名单内：%r" % name)
    path = paths.resolve_within(run_root, rel_path)
    result = logtail.tail_file(path, nbytes)
    result["path"] = pure
    return result
