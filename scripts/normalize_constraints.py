#!/usr/bin/env python3
"""Normalize extracted operator constraints before case generation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TENSOR_TYPES = {"aclTensor", "aclTensorList"}


def _attribute_groups(section: Any):
    if not isinstance(section, dict):
        return
    for parameter in section.values():
        if not isinstance(parameter, dict):
            continue
        if "type" in parameter:
            yield parameter
            continue
        for attributes in parameter.values():
            if isinstance(attributes, dict):
                yield attributes


def _type_name(attributes: dict[str, Any]) -> str:
    raw_type = attributes.get("type")
    if isinstance(raw_type, dict):
        raw_type = raw_type.get("value")
    if not isinstance(raw_type, str):
        return ""
    return re.sub(r"\b(?:const|struct)\b|[*&]", "", raw_type).strip()


def normalize_constraints(value: dict[str, Any]) -> int:
    """Clear dimensions for all non-Tensor input and output parameters."""
    normalized_count = 0
    for section_name in ("inputs", "outputs"):
        for attributes in _attribute_groups(value.get(section_name, {})):
            if _type_name(attributes) in TENSOR_TYPES:
                continue
            dimensions = attributes.get("dimensions")
            if isinstance(dimensions, dict):
                if dimensions.get("value") != []:
                    dimensions["value"] = []
                    normalized_count += 1
            elif dimensions != {"value": [], "src_text": ""}:
                attributes["dimensions"] = {"value": [], "src_text": ""}
                normalized_count += 1
    return normalized_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="constraints.json path (normalized in place)")
    args = parser.parse_args()

    path = Path(args.path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("constraints must be a JSON object")

    normalized_count = normalize_constraints(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"normalized": normalized_count, "path": str(path)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
