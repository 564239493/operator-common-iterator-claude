"""Transform ATK case dtype ``double`` -> ``fp64`` in generated case JSON files.

Background
----------
ATK registers ``"double"`` as a SCALAR data type in ``data_standard.py``
(``DataTypeFloat`` with ``return float(input_data)``), which shadows the
built-in ``EXCEPT_TYPES`` ``double->fp64`` tensor substitution.  As a result
``double`` tensor params are scalarized across the whole chain
(dataset generation -> CPU golden -> device run).

The framework intends ``double`` / ``float64`` tensors to use the ``fp64``
tensor data generator (``DatasetTorchDtype``, torch.float64) — that is the
dtype name actually registered for tensors.  Writing ``fp64`` in the case
data routes tensors through the correct path with no code/site-packages
changes.

Usage
-----
Run as a post-generation step on the per-platform ATK case files::

    python scripts/transform_double_fp64.py <cases_*.json> [...]

The files are rewritten in place; a per-file replacement count is printed.
A no-op when the cases contain no ``double`` dtype.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Values under this key are rewritten.
_DTYPE_KEY = "dtype"
_SRC = "double"
_DST = "fp64"


def transform_double_to_fp64(node) -> int:
    """Recursively replace ``{"dtype": "double"}`` with ``{"dtype": "fp64"}``.

    Returns the number of replacements made.  Traverses dicts (including
    ``inputs`` / ``outputs`` entries and nested parameter cards) and lists.
    """
    count = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if key == _DTYPE_KEY and value == _SRC:
                node[key] = _DST
                count += 1
            else:
                count += transform_double_to_fp64(value)
    elif isinstance(node, list):
        for item in node:
            count += transform_double_to_fp64(item)
    return count


def transform_file(path: Path) -> int:
    """Rewrite ``path`` (a case JSON array) in place; return replacement count."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    count = transform_double_to_fp64(payload)
    if count:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="generated ATK case JSON files to transform in place",
    )
    args = parser.parse_args()

    total = 0
    for path in args.files:
        count = transform_file(path)
        print(f"{path}: {count} dtype double->fp64")
        total += count
    print(f"TOTAL {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())