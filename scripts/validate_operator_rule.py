#!/usr/bin/env python3
"""Validate one JSON file against the repository's exact OperatorRule model."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "agent" / "generators" / "common_model_definition.py"


def load_operator_rule():
    spec = importlib.util.spec_from_file_location("operator_rule_contract", MODEL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load model module: {MODEL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OperatorRule


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate JSON with common_model_definition.OperatorRule.")
    parser.add_argument("json_file")
    args = parser.parse_args()
    path = Path(args.json_file).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        load_operator_rule()(**payload)
    except Exception as exc:
        print(json.dumps({"valid": False, "file": str(path), "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"valid": True, "file": str(path), "model": str(MODEL_PATH)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
