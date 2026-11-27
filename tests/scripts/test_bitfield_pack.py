"""scripts/bitfield_pack.py 单元测试（纯标准库）。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import bitfield_pack as bp
from scripts.route_aclnn_knowledge import route

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "knowledge" / "aclnn" / "reference" / "bitfield_pack.md"


def test_script_table_is_internally_consistent():
    assert bp.self_test() == {"ok": True, "rows": len(bp.TABLE), "errors": []}


def test_known_packed_values_match_document_evidence():
    assert bp.pack("group_size_v1", [1, 128, 128]) == 4303356032
    assert bp.pack("group_size_v1", [128, 128, 128]) == 549764202624
    assert bp.pack("group_size_v1", [1, 1, 32]) == 4295032864
    assert bp.pack("group_size_v1", [0, 0, 32]) == 32
    assert bp.pack("group_size_v1", [0, 1, 0]) == 65536
    assert bp.pack("group_size_v1", [0, 1, 32]) == 65568
    assert bp.pack("group_size_v1", [1, 0, 0]) == 4294967296
    assert bp.pack("group_size_v1", [1, 0, 32]) == 4294967328
    assert bp.pack("group_size_v1", [1, 1, 0]) == 4295032832
    assert bp.pack("group_size_v1", [0, 0, 256]) == 256
    assert bp.pack("block_size_v1", [0, 0, 0]) == 0


def test_lookup_hits_fixed_table():
    result = bp.lookup("group_size_v1", [1, 128, 128])
    assert result["found"] is True
    assert result["scenario"] == "G-B量化"
    assert result["packed"] == 4303356032
    assert result["source"].startswith("aclnnQuantMatmulV5")


def test_lookup_hits_mx_adaptive_combinations():
    expected = {
        (0, 1, 0): 65536,
        (0, 1, 32): 65568,
        (1, 0, 0): 4294967296,
        (1, 0, 32): 4294967328,
        (1, 1, 0): 4295032832,
    }
    for components, packed in expected.items():
        result = bp.lookup("group_size_v1", list(components))
        assert result["found"] is True
        assert result["scenario"] == "MX自适应组合"
        assert result["packed"] == packed


def test_lookup_computes_unknown_combination():
    result = bp.lookup("group_size_v1", [2, 64, 16])
    assert result["found"] is False
    assert result["packed"] == bp.pack("group_size_v1", [2, 64, 16])
    assert result["source"] == "computed"


def test_verify_rejects_wrong_value():
    assert bp.verify("group_size_v1", [1, 128, 128], 4303356032)["ok"] is True
    assert bp.verify("group_size_v1", [1, 128, 128], 4303356033)["ok"] is False


def test_unpack_roundtrip():
    packed = bp.pack("group_size_v1", [1, 128, 128])
    result = bp.unpack("group_size_v1", packed)
    assert result["components"] == {
        "groupSizeM": 1,
        "groupSizeN": 128,
        "groupSizeK": 128,
    }
    assert result["high_bits"] == 0


def test_out_of_range_component_rejected():
    with pytest.raises(bp.BitfieldError):
        bp.pack("group_size_v1", [65536, 0, 0])


def test_markdown_table_matches_script_table():
    assert bp.check_doc_table(DOC) == []


def test_check_doc_table_detects_drift(tmp_path):
    text = DOC.read_text(encoding="utf-8").replace("4303356032", "4303356033", 1)
    drifted = tmp_path / "bitfield_pack.md"
    drifted.write_text(text, encoding="utf-8")

    errors = bp.check_doc_table(drifted)

    assert any("packed" in error for error in errors)


def test_route_loads_bitfield_for_formula_doc(tmp_path):
    doc = tmp_path / "bitfield.md"
    doc.write_text(
        "# aclnnDemo\n\ngroupSize = groupSizeK | groupSizeN << 16 | groupSizeM << 32\n",
        encoding="utf-8",
    )

    assert "bitfield_pack" in route(doc)["resolved_modules"]


def test_route_ignores_plain_blocksize(tmp_path):
    doc = tmp_path / "paged_attention.md"
    doc.write_text(
        "# aclnnPagedAttention\n\nint64_t blockSize,\nblockSize 表示分页注意力的块大小。\n",
        encoding="utf-8",
    )

    assert "bitfield_pack" not in route(doc)["resolved_modules"]


def test_route_does_not_load_bitfield_for_quant_without_signal(tmp_path):
    doc = tmp_path / "quant.md"
    doc.write_text(
        "# aclnnDemo\n\n完成反量化计算，支持 per-tensor 量化。\n",
        encoding="utf-8",
    )

    assert "bitfield_pack" not in route(doc)["resolved_modules"]


def test_cli_check_doc_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "bitfield_pack.py"), "check-doc"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is True
