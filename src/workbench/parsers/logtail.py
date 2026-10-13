"""大日志尾部读取（绝不整读 generation_console.log 级别的文件）。"""
from pathlib import Path

from .. import config


def tail_file(path, nbytes=None):
    # type: (Path, int) -> dict
    """读取文件尾部 nbytes 字节，返回 {text, total_bytes, truncated}。"""
    limit = nbytes or config.LOG_TAIL_DEFAULT_BYTES
    limit = max(1, min(limit, config.LOG_TAIL_MAX_BYTES))
    size = path.stat().st_size
    start = max(0, size - limit)
    with open(str(path), "rb") as fh:
        fh.seek(start)
        chunk = fh.read()
    if start > 0:
        # 丢弃可能截断的首行
        nl = chunk.find(b"\n")
        if 0 <= nl < len(chunk) - 1:
            chunk = chunk[nl + 1:]
    text = chunk.decode("utf-8", errors="replace")
    return {"text": text, "total_bytes": size, "truncated": start > 0}
