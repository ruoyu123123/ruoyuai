"""scaffold_genre_packs 专属回归 — 聚焦既有 test_genre_packs.py 未覆盖的核心确定性逻辑。

既有 test_genre_packs.py 已锁：_verify() 正路、canonical_genres、get_pack 基本路由、
get_scanner、get_writer_directives、scanner advisory 边界、manifest 注入。

本文件补盲区（绝不重复既有断言）：
  · get_judge_dims（既有完全未测）
  · get_pack 的 .strip().lower() 大小写 + 空白归一（既有只测原样 key）
  · _load() 缓存语义（同一 dict 复用）
  · main() CLI 分发 + 退出码（--list / --pack / verify / 默认 help）
  · _verify() 各失败分支（坏 JSON / 坏 canonical / 坏 packs / genre 不在 canonical / 缺 judge_dims）
    —— 经临时 JSON 注入 PACKS_FILE + 清缓存触发，钉死退出码 2。
"""
import importlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import scaffold_genre_packs as gp  # noqa: E402


# ---------- 缓存隔离工具：临时把 PACKS_FILE 指到自造 JSON 跑被测函数 ----------

def _with_packs_json(payload, fn):
    """把模块的 PACKS_FILE 临时换成写有 payload 的临时文件，清缓存，跑 fn()，恢复。

    payload=str → 原样写（用于制造坏 JSON）；否则 json.dumps。
    """
    saved_file = gp.PACKS_FILE
    saved_cache = gp._CACHE
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "genre_dimension_packs.json"
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        p.write_text(text, encoding="utf-8")
        gp.PACKS_FILE = p
        gp._CACHE = None
        try:
            return fn()
        finally:
            gp.PACKS_FILE = saved_file
            gp._CACHE = saved_cache


# ---------- get_judge_dims（既有完全未覆盖） ----------

def test_get_judge_dims_known_genre():
    """romance 题材包 judge_dims 非空且含已知维度键 R1。"""
    jd = gp.get_judge_dims("romance")
    assert isinstance(jd, dict) and jd, "romance 应有 judge_dims"
    assert any(k.startswith("R1") for k in jd), f"缺 R1_* 维度: {list(jd)}"


def test_get_judge_dims_horror_game():
    """horror_game judge_dims 含面板维度 G1。"""
    jd = gp.get_judge_dims("horror_game")
    assert any(k.startswith("G1") for k in jd), f"缺 G1_* 维度: {list(jd)}"


def test_get_judge_dims_unknown_and_none_empty():
    """unknown / 无包 / None → {}（退化纯通用池·零回归）。"""
    assert gp.get_judge_dims("unknown") == {}
    assert gp.get_judge_dims(None) == {}
    assert gp.get_judge_dims("不存在的题材") == {}


def test_get_judge_dims_v2_new_genres():
    """v2 新增 5 个题材包都有 judge_dims。"""
    for genre in ("xuanhuan", "xianxia", "urban_supernatural", "scifi_meta", "historical"):
        jd = gp.get_judge_dims(genre)
        assert isinstance(jd, dict) and jd, f"{genre} 应有 judge_dims"


# ---------- get_pack 归一化：.strip().lower()（既有只测原样小写 key） ----------

def test_get_pack_case_insensitive():
    """大写题材名归一到 lower 命中同一包。"""
    base = gp.get_pack("romance")
    assert gp.get_pack("ROMANCE") == base
    assert gp.get_pack("Romance") == base
    assert base, "romance 包不应为空"


def test_get_pack_strips_whitespace():
    """前后空白归一后命中。"""
    assert gp.get_pack("  romance  ") == gp.get_pack("romance")
    assert gp.get_pack("\thorror_game\n") == gp.get_pack("horror_game")


def test_get_pack_returns_copy_safe_to_consume():
    """get_pack 返回真实包内容（含 scanner / writer_directives / judge_dims 三键）。"""
    pk = gp.get_pack("horror_game")
    assert pk.get("scanner") == "litrpg_structure_scanner"
    assert isinstance(pk.get("judge_dims"), dict) and pk["judge_dims"]
    assert isinstance(pk.get("writer_directives"), list) and pk["writer_directives"]


# ---------- _load 缓存语义 ----------

def test_load_caches_same_object():
    """_load() 命中缓存 → 两次返回同一 dict 对象（is 相等）。"""
    a = gp._load()
    b = gp._load()
    assert a is b, "_load 应复用 _CACHE 同一对象"


def test_load_reads_injected_file():
    """换 PACKS_FILE + 清缓存后 _load 读到新内容（证明缓存可被重置）。"""
    payload = {"_canonical_genres": ["only_one"], "packs": {}}
    got = _with_packs_json(payload, lambda: gp._load())
    assert got["_canonical_genres"] == ["only_one"]
    # 恢复后真实文件仍可正常加载（缓存已还原）
    assert "romance" in gp.canonical_genres()


# ---------- main() CLI 分发 + 退出码 ----------

def _run_main(argv):
    """以给定 argv 跑 main()，捕获 (rc, stdout, stderr)。"""
    saved = sys.argv
    out, err = io.StringIO(), io.StringIO()
    sys.argv = ["scaffold_genre_packs.py"] + argv
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = gp.main()
    finally:
        sys.argv = saved
    return rc, out.getvalue(), err.getvalue()


def test_main_list_exit0():
    rc, out, _ = _run_main(["--list"])
    assert rc == 0
    assert "canonical genres" in out
    assert "romance" in out


def test_main_pack_prints_json_exit0():
    rc, out, _ = _run_main(["--pack", "romance"])
    assert rc == 0
    parsed = json.loads(out)              # 必须是合法 JSON
    assert parsed.get("scanner") == "romance_pacing_scanner"


def test_main_pack_unknown_prints_empty_json():
    """--pack unknown → 打印 {}（退化），仍退出 0。"""
    rc, out, _ = _run_main(["--pack", "unknown"])
    assert rc == 0
    assert json.loads(out) == {}


def test_main_pack_missing_arg_no_crash():
    """--pack 末尾无参数 → 不索引越界，打印 {} 退出 0。"""
    rc, out, _ = _run_main(["--pack"])
    assert rc == 0
    assert json.loads(out) == {}


def test_main_verify_real_file_exit0():
    rc, out, _ = _run_main(["verify"])
    assert rc == 0
    assert "[OK]" in out


def test_main_default_prints_doc_exit0():
    """无识别参数 → 打印 __doc__ 帮助，退出 0。"""
    rc, out, _ = _run_main([])
    assert rc == 0
    assert "scaffold_genre_packs" in out


# ---------- _verify() 失败分支（退出码 2） ----------

def test_verify_corrupt_json_returns_2():
    rc = _with_packs_json("{ this is not json ", lambda: gp._verify())
    assert rc == 2


def test_verify_missing_canonical_returns_2():
    rc = _with_packs_json({"packs": {}}, lambda: gp._verify())
    assert rc == 2


def test_verify_canonical_not_list_returns_2():
    rc = _with_packs_json({"_canonical_genres": "romance", "packs": {}}, lambda: gp._verify())
    assert rc == 2


def test_verify_packs_not_dict_returns_2():
    rc = _with_packs_json(
        {"_canonical_genres": ["romance"], "packs": ["romance"]}, lambda: gp._verify())
    assert rc == 2


def test_verify_pack_genre_not_in_canonical_returns_2():
    """packs 含一个不在 _canonical_genres 的 genre → 退出 2。"""
    payload = {
        "_canonical_genres": ["romance"],
        "packs": {"romance": {"judge_dims": {"R1": "x"}},
                  "ghost_genre": {"judge_dims": {"X": "y"}}},
    }
    rc = _with_packs_json(payload, lambda: gp._verify())
    assert rc == 2


def test_verify_pack_missing_judge_dims_returns_2():
    """canonical 内 genre 但 judge_dims 非 dict → 退出 2。"""
    payload = {
        "_canonical_genres": ["romance"],
        "packs": {"romance": {"judge_dims": "not_a_dict"}},
    }
    rc = _with_packs_json(payload, lambda: gp._verify())
    assert rc == 2


def test_verify_minimal_valid_returns_0():
    """最小合法结构 → 退出 0（与失败分支形成对照，确认不是恒为 2）。"""
    payload = {
        "_canonical_genres": ["romance", "unknown"],
        "packs": {"romance": {"judge_dims": {"R1": "x"}}},
    }
    rc = _with_packs_json(payload, lambda: gp._verify())
    assert rc == 0


# ---------- 自跑入口（参照既有约定） ----------

def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[scaffold_genre_packs] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
