#!/usr/bin/env python3
"""Batch-level device inventory and selection.

Batch runs must narrow by an explicit device before the first operator is
claimed. This module deterministically scans every document in a batch for its
"产品支持情况" section, exposes the union of devices together with per-document
support, and records the resolved device on ``batch_state.json``.

The coordinator still performs the confirmation UX (same device-first flow as a
single ``/iterate-operator`` run); this script only owns the deterministic
parts: parsing device tables, validating the selected device, and skipping
documents that do not support it. Quantization scenes are NOT selected here:
each single-operator run reads its own document and asks for the scenes under
the already-confirmed device.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from batch_state import (
    SKIPPED_STATE,
    load_batch,
    resolve_batch_dir,
    save_batch,
    utc_now,
)

_UNPARSED = "unparsed"
_SUPPORTED = "supported"
_PARTIAL = "partial"
_UNSUPPORTED = "unsupported"
_NOT_LISTED = "not_listed"

_HEADING_RE = re.compile(r"(?m)^##\s+[^\n]*产品支持情况[^\n]*$")
_NEXT_SECTION_RE = re.compile(r"(?m)^#{1,2}\s+")
_BULLET_RE = re.compile(r"<term>\s*(.*?)\s*</term>\s*[：:]\s*([^\n]+)", re.S)
_PLAIN_BULLET_RE = re.compile(
    r"(?m)^\s*[-*]\s*(?!<term>)([^：:\n]+?)\s*[：:]\s*(支持|不支持)\s*$"
)
_SUPPORT_HEADER_RE = re.compile(r"是否\s*支持|产品名称|^产品$")


def _clean_name(raw: str) -> str:
    name = re.sub(r"<[^>]+>", "", raw)
    name = name.replace("&nbsp;", " ").replace("\u3000", " ")
    name = name.replace("**", "")
    name = re.sub(r"\s+", " ", name)
    return name.strip(" *`|")


def _parse_marker(raw: str) -> bool | None:
    text = raw.strip()
    if not text:
        return None
    if "不支持" in text or "不满足" in text:
        return False
    if "支持" in text:
        return True
    if any(mark in text for mark in ("√", "✓", "☑")):
        return True
    if any(mark in text for mark in ("×", "✗", "✘")):
        return False
    lowered = text.casefold()
    if lowered in {"yes", "y", "true"}:
        return True
    if lowered in {"no", "n", "false"}:
        return False
    return None


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        not cell or re.fullmatch(r":?-{2,}:?", cell) for cell in cells
    )


def _support_section(text: str) -> str:
    match = _HEADING_RE.search(text)
    if match is None:
        return ""
    start = match.end()
    following = _NEXT_SECTION_RE.search(text, start)
    return text[start: following.start() if following else len(text)]


def extract_device_support(text: str) -> dict[str, bool]:
    """Return ``{device full name: supported}`` from one operator document."""
    section = _support_section(text)
    if not section:
        return {}

    parsed: dict[str, bool] = {}

    def add(name: str, supported: bool) -> None:
        cleaned = _clean_name(name)
        if not cleaned:
            return
        parsed[cleaned] = parsed.get(cleaned, False) or supported

    for match in _BULLET_RE.finditer(section):
        marker = _parse_marker(match.group(2))
        if marker is not None:
            add(match.group(1), marker)

    for match in _PLAIN_BULLET_RE.finditer(section):
        add(match.group(1), match.group(2) == "支持")

    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or _is_separator(cells):
            continue
        if any(_SUPPORT_HEADER_RE.search(cell) for cell in cells):
            continue
        name_cell = next((cell for cell in cells if "<term>" in cell), cells[0])
        marker = None
        for cell in reversed(cells[1:]):
            marker = _parse_marker(cell)
            if marker is not None:
                break
        if marker is None:
            continue
        add(name_cell, marker)
    return parsed


def canonical_device(name: str) -> str:
    """Collapse documented device-name variants into one selectable family."""
    compact = re.sub(r"\s+", "", name).casefold()
    if "200i" in compact or "500a2" in compact:
        return "Atlas 200I/500 A2 推理产品"
    if "950pr" in compact or "950dt" in compact or "ascend950" in compact:
        return "Ascend 950PR/950DT 系列"
    if "a3" in compact:
        return "Atlas A3 系列"
    if "a2" in compact:
        return "Atlas A2 系列"
    if "350" in compact:
        return "Atlas 350 加速卡"
    if "推理系列加速卡" in compact:
        return "Atlas 推理系列加速卡"
    if "推理系列" in compact:
        return "Atlas 推理系列"
    if "训练系列" in compact:
        return "Atlas 训练系列"
    return name


def _document_entry(operator: dict[str, Any]) -> dict[str, Any]:
    path = Path(operator["operator_doc_source"])
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    raw = extract_device_support(text)
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"names": [], "values": [], "supported_names": []}
    )
    for name, supported in raw.items():
        canonical = canonical_device(name)
        grouped[canonical]["names"].append(name)
        grouped[canonical]["values"].append(supported)
        if supported:
            grouped[canonical]["supported_names"].append(name)

    devices: dict[str, dict[str, Any]] = {}
    for canonical, info in grouped.items():
        values = info["values"]
        if any(values) and any(not value for value in values):
            status = _PARTIAL
        elif any(values):
            status = _SUPPORTED
        else:
            status = _UNSUPPORTED
        devices[canonical] = {
            "device": canonical,
            "names": sorted(set(info["names"])),
            "supported_names": sorted(set(info["supported_names"])),
            "status": status,
        }
    return {
        "index": operator["index"],
        "relative_path": operator.get("relative_path", ""),
        "operator_doc_source": str(path),
        "parsed": bool(raw),
        "devices": devices,
    }


def build_inventory(batch: dict[str, Any]) -> dict[str, Any]:
    documents = [_document_entry(operator) for operator in batch["operators"]]
    devices: dict[str, dict[str, Any]] = {}
    for document in documents:
        for canonical, info in document["devices"].items():
            entry = devices.setdefault(
                canonical,
                {
                    "device": canonical,
                    "aliases": set(),
                    "supported_documents": [],
                    "partial_documents": [],
                    "unsupported_documents": [],
                },
            )
            entry["aliases"].update(info["names"])
            status = info["status"]
            if status == _SUPPORTED:
                entry["supported_documents"].append(document["index"])
            elif status == _PARTIAL:
                entry["partial_documents"].append(document["index"])
            else:
                entry["unsupported_documents"].append(document["index"])

    unparsed_documents = [doc["index"] for doc in documents if not doc["parsed"]]
    for entry in devices.values():
        known = set(
            entry["supported_documents"]
            + entry["partial_documents"]
            + entry["unsupported_documents"]
            + unparsed_documents
        )
        entry["not_listed_documents"] = [
            doc["index"]
            for doc in documents
            if doc["index"] not in known
        ]
        entry["unparsed_documents"] = list(unparsed_documents)
        entry["aliases"] = sorted(entry["aliases"])

    return {
        "total_documents": len(documents),
        "devices": sorted(devices.values(), key=lambda item: item["device"]),
        "documents": documents,
        "unparsed_documents": unparsed_documents,
    }


_DEVICE_KEYWORDS = (
    ("950pr", "Ascend 950PR/950DT 系列"),
    ("950dt", "Ascend 950PR/950DT 系列"),
    ("ascend950", "Ascend 950PR/950DT 系列"),
    ("950", "Ascend 950PR/950DT 系列"),
    ("200i", "Atlas 200I/500 A2 推理产品"),
    ("500a2", "Atlas 200I/500 A2 推理产品"),
    ("310b", "Atlas 200I/500 A2 推理产品"),
    ("a3", "Atlas A3 系列"),
    ("910b", "Atlas A2 系列"),
    ("910", "Atlas 训练系列"),
    ("a2", "Atlas A2 系列"),
    ("310p", "Atlas 推理系列"),
    ("350", "Atlas 350 加速卡"),
    ("推理系列加速卡", "Atlas 推理系列加速卡"),
    ("推理系列", "Atlas 推理系列"),
    ("训练系列", "Atlas 训练系列"),
)


def _match_device(text: str, inventory: dict[str, Any]) -> tuple[str | None, list[str]]:
    lowered = text.casefold()
    compact = re.sub(r"\s+", "", lowered)
    candidates: list[str] = []
    for entry in inventory["devices"]:
        device = entry["device"]
        if device.casefold() in lowered or re.sub(r"\s+", "", device).casefold() in compact:
            candidates.append(device)
            continue
        if any(alias.casefold() in lowered for alias in entry["aliases"]):
            candidates.append(device)
    if not candidates:
        matched_flags: set[str] = set()
        for keyword, device in _DEVICE_KEYWORDS:
            if keyword not in compact:
                continue
            if keyword == "a2" and matched_flags & {"200i", "500a2"}:
                continue
            if keyword == "910" and "910b" in matched_flags:
                continue
            if keyword == "950" and matched_flags & {"950pr", "950dt", "ascend950"}:
                continue
            if keyword == "推理系列" and "推理系列加速卡" in matched_flags:
                continue
            matched_flags.add(keyword)
            if device not in candidates:
                candidates.append(device)
    return (candidates[0] if len(candidates) == 1 else None), candidates


def _document_status(
    document: dict[str, Any], device: str
) -> str:
    if not document["parsed"]:
        return _UNPARSED
    info = document["devices"].get(device)
    return info["status"] if info else _NOT_LISTED


def _resolve(
    batch_dir: Path, batch: dict[str, Any], args: argparse.Namespace
) -> tuple[int, dict[str, Any]]:
    inventory = build_inventory(batch)
    if not inventory["devices"]:
        return 2, {
            "ok": False,
            "requires_user_action": True,
            "code": "NO_DEVICE_INFORMATION",
            "message": (
                "批次内所有算子文档都没有可解析的“产品支持情况”设备信息，"
                "无法在批处理级别选择设备；请改用单算子 /iterate-operator --scenes。"
            ),
        }

    raw_text = (args.scenes or "").strip()
    device = (args.device or "").strip()
    if not device:
        matched, candidates = _match_device(raw_text, inventory)
        if len(candidates) > 1:
            return 2, {
                "ok": False,
                "requires_user_action": True,
                "code": "DEVICE_AMBIGUOUS",
                "message": "场景文本命中多个设备，请用 --device 指定一个设备。",
                "candidates": candidates,
                "inventory": inventory,
            }
        if matched is None:
            return 2, {
                "ok": False,
                "requires_user_action": True,
                "code": "DEVICE_REQUIRED",
                "message": (
                    "未能从场景文本中识别设备类型；设备是批处理场景的硬性必要项。"
                    "请从 devices 中选择一个设备，或补充设备全称后重试。"
                ),
                "inventory": inventory,
            }
        device = matched

    known = {entry["device"] for entry in inventory["devices"]}
    if device not in known:
        return 2, {
            "ok": False,
            "requires_user_action": True,
            "code": "DEVICE_NOT_FOUND",
            "message": f"设备 {device!r} 不在批次文档的设备清单中。",
            "devices": sorted(known),
        }

    scene_text = raw_text or device
    source = args.source or (
        "scenes_param" if batch.get("scenes_from_cli") else "interactive_input"
    )
    if source not in {"scenes_param", "interactive_input"}:
        return 2, {
            "ok": False,
            "code": "INVALID_SCENE_SOURCE",
            "message": "source 只能是 scenes_param 或 interactive_input。",
        }

    by_index = {doc["index"]: doc for doc in inventory["documents"]}
    statuses: dict[str, list[int]] = {
        _SUPPORTED: [],
        _PARTIAL: [],
        _UNSUPPORTED: [],
        _NOT_LISTED: [],
        _UNPARSED: [],
    }
    for operator in batch["operators"]:
        document = by_index[operator["index"]]
        status = _document_status(document, device)
        statuses[status].append(operator["index"])
        operator["scene_status"] = status
        info = document["devices"].get(device)
        if info and info.get("supported_names"):
            alias = sorted(info["supported_names"], key=len, reverse=True)[0]
        elif info and info["names"]:
            alias = sorted(info["names"], key=len, reverse=True)[0]
        else:
            alias = device
        lowered = scene_text.casefold()
        operator["scene_text"] = (
            scene_text
            if alias.casefold() in lowered
            else f"{alias} {scene_text}"
        )

    should_skip = set(statuses[_UNSUPPORTED] + statuses[_NOT_LISTED])
    now = utc_now()
    skipped: list[int] = []
    restored: list[int] = []
    for operator in batch["operators"]:
        index = operator["index"]
        if args.skip_unsupported and index in should_skip:
            if operator["status"] in {"PENDING", SKIPPED_STATE}:
                if operator["status"] != SKIPPED_STATE:
                    skipped.append(index)
                operator["status"] = SKIPPED_STATE
                operator["terminal_state"] = "SKIPPED_UNSUPPORTED_DEVICE"
                operator["run_state"] = "SKIPPED_UNSUPPORTED_DEVICE"
                operator["message"] = f"设备 {device} 不受该算子文档支持，已跳过"
                operator["completed_at"] = now
        elif operator["status"] == SKIPPED_STATE:
            operator["status"] = "PENDING"
            operator["terminal_state"] = None
            operator["run_state"] = None
            operator["message"] = ""
            operator["completed_at"] = None
            restored.append(index)

    batch["scenes"] = scene_text
    batch["scene"] = {
        "source": source,
        "raw": scene_text,
        "device": device,
        "resolved_at": now,
        "skip_unsupported": bool(args.skip_unsupported),
        "support": {
            "supported": statuses[_SUPPORTED],
            "partial": statuses[_PARTIAL],
            "unsupported": statuses[_UNSUPPORTED],
            "not_listed": statuses[_NOT_LISTED],
            "unparsed": statuses[_UNPARSED],
        },
        "inventory": inventory,
    }
    batch["history"].append(
        {
            "event": "SCENE_RESOLVED",
            "device": device,
            "scenes": scene_text,
            "skipped": skipped,
            "at": now,
        }
    )
    if skipped:
        batch["history"].append(
            {
                "event": "OPERATORS_SKIPPED_UNSUPPORTED_DEVICE",
                "indices": skipped,
                "at": now,
            }
        )
    if restored:
        batch["history"].append(
            {
                "event": "SKIPPED_OPERATORS_RESTORED",
                "indices": restored,
                "at": now,
            }
        )
    save_batch(batch_dir, batch)

    unsupported = sorted(
        statuses[_UNSUPPORTED] + statuses[_NOT_LISTED] + statuses[_UNPARSED]
    )
    warnings: list[str] = []
    if statuses[_UNSUPPORTED]:
        warnings.append(
            "以下算子文档明确不支持该设备: "
            + ", ".join(str(i) for i in statuses[_UNSUPPORTED])
        )
    if statuses[_NOT_LISTED]:
        warnings.append(
            "以下算子文档的设备表中未列出该设备: "
            + ", ".join(str(i) for i in statuses[_NOT_LISTED])
        )
    if statuses[_PARTIAL]:
        warnings.append(
            "以下算子文档仅部分设备变体支持该设备族: "
            + ", ".join(str(i) for i in statuses[_PARTIAL])
        )
    if statuses[_UNPARSED]:
        warnings.append(
            "以下算子文档没有可解析的设备表，支持性无法判断: "
            + ", ".join(str(i) for i in statuses[_UNPARSED])
        )

    return 0, {
        "ok": True,
        "batch_dir": str(batch_dir),
        "device": device,
        "scenes": scene_text,
        "source": source,
        "support": batch["scene"]["support"],
        "unsupported_or_unknown_documents": unsupported,
        "skipped_documents": skipped,
        "restored_documents": restored,
        "warnings": warnings,
        "documents": [
            {
                "index": operator["index"],
                "relative_path": operator.get("relative_path", ""),
                "status": operator.get("scene_status"),
            }
            for operator in batch["operators"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="批处理设备清单与场景选择。")
    parser.add_argument("--batch-dir", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inventory", help="列出批次全部设备及逐文档支持情况")
    resolve = subparsers.add_parser("resolve", help="确认批次场景并写入 batch_state")
    resolve.add_argument("--scenes", default="", help="用户场景文本描述")
    resolve.add_argument(
        "--device",
        default="",
        help="从 inventory.devices 中选择的设备族；省略时从 --scenes 文本匹配",
    )
    resolve.add_argument(
        "--skip-unsupported",
        action="store_true",
        help="跳过明确不支持或未列出该设备的算子文档",
    )
    resolve.add_argument(
        "--source",
        choices=("scenes_param", "interactive_input"),
        default=None,
        help="场景来源；省略时按初始化是否带 --scenes 推断",
    )
    args = parser.parse_args()

    try:
        batch_dir = resolve_batch_dir(args.batch_dir)
        batch = load_batch(batch_dir)
        if args.command == "inventory":
            scene = batch.get("scene")
            result = {
                "ok": True,
                "batch_dir": str(batch_dir),
                "scene": (
                    {key: value for key, value in scene.items() if key != "inventory"}
                    if isinstance(scene, dict)
                    else None
                ),
                **build_inventory(batch),
            }
            code = 0
        else:
            code, result = _resolve(batch_dir, batch, args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps(
            {"ok": False, "code": "BATCH_SCENE_ERROR", "message": str(exc)},
            ensure_ascii=False,
            indent=2,
        ))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
