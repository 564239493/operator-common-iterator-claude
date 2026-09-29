#!/usr/bin/env python3
"""算子测试工作台 · 可视化审核服务入口。

纯标准库实现，零 pip 依赖。读取运行产物；仅人工约束提交接口可写入 constraints_copy.json，不启动业务。

用法：
    python3 src/serve.py [--port 8420] [--host 127.0.0.1] [--root <项目根>]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workbench.server import run_server  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="算子测试工作台只读可视化服务")
    parser.add_argument("--port", type=int, default=8420, help="监听端口（默认 8420）")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址（默认 127.0.0.1，仅本机）")
    parser.add_argument("--root", default=None, help="项目根目录（默认按本文件位置自动推断）")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, root=args.root)


if __name__ == "__main__":
    main()
