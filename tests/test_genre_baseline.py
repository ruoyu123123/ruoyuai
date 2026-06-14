"""C3 genre 中性基线测试（2026-06-14·G6-GENREBASE diff-against-average）。

守护（北极星⑤·作者档第一权威·通用基线只兜底）：
  1. json 合法 + 标 _authority=FALLBACK_NOT_AUTHORITATIVE（非权威）；
  2. baseline 键 ⊆ replication_fidelity._BANDS（保证 diff 可对齐 _author_baseline 输出）；
  3. 文件在约定目录 core/claude-home/templates/ 存在（与 genre_dimension_packs.json 同级）。
零依赖（stdlib）。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

_GB_PATH = _ROOT / "core" / "claude-home" / "templates" / "genre_baseline.json"


def test_genre_baseline_json_valid():
    d = json.loads(_GB_PATH.read_text(encoding="utf-8"))
    assert "baseline" in d and isinstance(d["baseline"], dict)
    assert "_direction_labels" in d and isinstance(d["_direction_labels"], dict)
    assert d.get("_authority") == "FALLBACK_NOT_AUTHORITATIVE"


def test_genre_baseline_keys_match_metrics():
    import replication_fidelity_check as rfc
    d = json.loads(_GB_PATH.read_text(encoding="utf-8"))
    extra = set(d["baseline"].keys()) - set(rfc._BANDS.keys())
    assert not extra, f"baseline 含 _BANDS 没有的键(无法对齐 diff): {extra}"


def test_genre_baseline_in_templates_dir():
    assert _GB_PATH.exists(), f"genre_baseline.json 应在 {_GB_PATH}"
