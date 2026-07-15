#!/usr/bin/env python3
"""Normalize extracted operator constraints before case generation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TENSOR_TYPES = {"aclTensor", "aclTensorList"}
HS_TYPE_ALIASES = {
    "Tensor": "aclTensor",
    "torch.Tensor": "aclTensor",
    "Optional[Tensor]": "aclTensor",
    "Optional[torch.Tensor]": "aclTensor",
    "List[Tensor]": "aclTensorList",
    "list[Tensor]": "aclTensorList",
    "Sequence[Tensor]": "aclTensorList",
    "Tensor[]": "aclTensorList",
    "List[int]": "aclIntArray",
    "list[int]": "aclIntArray",
    "Sequence[int]": "aclIntArray",
    "int[]": "aclIntArray",
    "List[float]": "aclFloatArray",
    "list[float]": "aclFloatArray",
    "float[]": "aclFloatArray",
    "List[bool]": "aclBoolArray",
    "list[bool]": "aclBoolArray",
    "bool[]": "aclBoolArray",
    "str": "string",
}
ARRAY_TYPE_DTYPE_FALLBACK = {
    "aclIntArray": "int",
    "aclFloatArray": "float",
    "aclBoolArray": "bool",
}
NULL_POINTER_ONLY_RE = re.compile(
    r"(只支持传空指针|传空指针|必须为空指针|仅支持空指针)"
)
FORMAT_SEPARATOR_RE = re.compile(r"[、，,/]")


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


def _normalize_type(attributes: dict[str, Any]) -> bool:
    """Translate torch_npu documentation types to the generator's IR types."""
    field = attributes.get("type")
    raw = field.get("value") if isinstance(field, dict) else field
    if not isinstance(raw, str):
        return False
    normalized = HS_TYPE_ALIASES.get(raw.strip())
    if not normalized or normalized == raw:
        return False
    if isinstance(field, dict):
        field["value"] = normalized
    else:
        attributes["type"] = {"value": normalized, "src_text": raw}
    return True


def _is_null_pointer_only(attributes: dict[str, Any]) -> bool:
    source_texts = [attributes.get("description", "")]
    for field_name in ("is_optional", "dtype"):
        field = attributes.get(field_name)
        if isinstance(field, dict):
            source_texts.append(field.get("src_text", ""))
    return any(
        isinstance(text, str) and NULL_POINTER_ONLY_RE.search(text)
        for text in source_texts
    )


def _normalize_tensor_format(attributes: dict[str, Any]) -> bool:
    """Normalize a Tensor format domain to ``ValueWithSrcText.value: list[str]``.

    The reference constraint builder always splits the stored format text into
    a list, including the single-format case.  Accept legacy flat strings here
    so previously extracted constraints remain usable.
    """
    raw_format = attributes.get("format")
    if isinstance(raw_format, dict):
        raw_value = raw_format.get("value")
        if not isinstance(raw_value, str):
            return False
        value = raw_value.strip()
        normalized = (
            []
            if not value or value.upper() == "N/A"
            else sorted({item.strip() for item in FORMAT_SEPARATOR_RE.split(value) if item.strip()})
        )
        raw_format["value"] = normalized
        return True
    if isinstance(raw_format, str):
        value = raw_format.strip()
        normalized = (
            []
            if not value or value.upper() == "N/A"
            else sorted({item.strip() for item in FORMAT_SEPARATOR_RE.split(value) if item.strip()})
        )
        attributes["format"] = {"value": normalized, "src_text": ""}
        return True
    return False


def normalize_constraints(value: dict[str, Any]) -> int:
    """Normalize type-dependent format, dimensions, and dtype values."""
    normalized_count = 0
    for section_name in ("inputs", "outputs"):
        for attributes in _attribute_groups(value.get(section_name, {})):
            if _normalize_type(attributes):
                normalized_count += 1
            type_name = _type_name(attributes)
            if type_name in TENSOR_TYPES:
                if _normalize_tensor_format(attributes):
                    normalized_count += 1
                continue

            dimensions = attributes.get("dimensions")
            if isinstance(dimensions, dict):
                if dimensions.get("value") != []:
                    dimensions["value"] = []
                    normalized_count += 1
            elif dimensions != {"value": [], "src_text": ""}:
                attributes["dimensions"] = {"value": [], "src_text": ""}
                normalized_count += 1

            dtype = attributes.get("dtype")
            dtype_value = dtype.get("value") if isinstance(dtype, dict) else dtype
            if dtype_value == [] and type_name and not _is_null_pointer_only(attributes):
                fallback = ARRAY_TYPE_DTYPE_FALLBACK.get(type_name, type_name)
                if isinstance(dtype, dict):
                    dtype["value"] = [fallback]
                else:
                    attributes["dtype"] = {"value": [fallback], "src_text": ""}
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
