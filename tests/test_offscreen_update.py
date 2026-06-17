"""offscreen_update 回归测试 — 守护 save-state offscreen 状态更新的核心契约。

钉死的不变量（全部来自脚本真实逻辑，非 mock）：
- offscreen.actions[idx].done 只能 false → true，绝不反向；已 done 跳过（幂等）。
- completed_fully=false / 缺 action_index / 角色找不到 / action_index 越界 → 跳过不写。
- 真写入时盖 done=true + _done_at_ch=<章号>，走 atomic_json 原子写。
- 退出码契约：changes 缺失=0 / executed 空=0 / 人物卡损坏=2 / dry-run=0 /
  有 skipped=1 / 全干净=0。

测试通过设置 sys.argv 调真实 main()，捕 SystemExit 读退出码，断言落盘 JSON。
零依赖：仅标准库；临时目录隔离，绝不碰真项目。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import offscreen_update as ou  # noqa: E402


# ---------------------------------------------------------------------------
# 工程：造一个最小项目（人物卡 + 第NNN章_changes.json）
# ---------------------------------------------------------------------------
def _mk_project(tmp: Path, characters=None, executed=None, ch=1, *,
                write_changes=True, changes_text=None):
    """造临时项目目录，返回 (project_root, cards_path)。

    characters: 人物卡 characters 列表（None → 不写人物卡文件）
    executed:   self_eval.offscreen_actions_executed 列表
    write_changes: False → 不写 changes.json（测「文件不存在」分支）
    changes_text:  非 None → 直接写这段原始文本（测损坏 JSON）
    """
    proj = tmp / "测试书"
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    cards_path = db / "人物卡.json"
    if characters is not None:
        cards_path.write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")

    ch_dir = proj / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    changes_path = ch_dir / f"第{ch:03d}章_changes.json"
    if write_changes:
        if changes_text is not None:
            changes_path.write_text(changes_text, encoding="utf-8")
        else:
            payload = {"self_eval": {"offscreen_actions_executed": executed or []}}
            changes_path.write_text(json.dumps(payload, ensure_ascii=False),
                                    encoding="utf-8")
    return proj, cards_path


def _run(project, ch, dry_run=False):
    """设 sys.argv 调真实 main()，返回退出码（捕 SystemExit）。"""
    argv = [str(project), str(ch)]
    if dry_run:
        argv.append("--dry-run")
    old = sys.argv
    sys.argv = ["offscreen_update.py"] + argv
    try:
        ou.main()
        return 0  # main 必 sys.exit，理论上到不了
    except SystemExit as e:
        return e.code if e.code is not None else 0
    finally:
        sys.argv = old


def _load_cards(cards_path: Path):
    return json.loads(cards_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# load_json 纯函数
# ---------------------------------------------------------------------------
def test_load_json_pure():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 不存在 → default
        assert ou.load_json(tmp / "missing.json", {"x": 1}) == {"x": 1}
        assert ou.load_json(tmp / "missing.json") is None
        # 正常 JSON（含中文，utf-8）
        good = tmp / "good.json"
        good.write_text(json.dumps({"名字": "江条款"}, ensure_ascii=False),
                        encoding="utf-8")
        assert ou.load_json(good) == {"名字": "江条款"}
        # 损坏 JSON → default
        bad = tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert ou.load_json(bad, {"fallback": True}) == {"fallback": True}


# ---------------------------------------------------------------------------
# happy path：completed_fully=true 的 action 被标 done
# ---------------------------------------------------------------------------
def test_happy_path_marks_done_and_records_chapter():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        chars = [{
            "name": "江条款",
            "offscreen": {"actions": [
                {"action": "潜入档案室", "done": False},
                {"action": "联络线人", "done": False},
            ]},
        }]
        executed = [{"character": "江条款", "action_index": 0,
                     "completed_fully": True, "evidence": "见正文第3段"}]
        proj, cards_path = _mk_project(tmp, characters=chars, executed=executed, ch=5)

        code = _run(proj, 5)
        cards = _load_cards(cards_path)
        actions = cards["characters"][0]["offscreen"]["actions"]
        # action[0] 被标 done + 记章号
        assert actions[0]["done"] is True
        assert actions[0]["_done_at_ch"] == 5
        # action[1] 未被触及
        assert actions[1]["done"] is False
        assert "_done_at_ch" not in actions[1]
        # 无 skipped → 退出 0
        assert code == 0


# ---------------------------------------------------------------------------
# 只能 false → true：已 done 的不被反向，且算 skipped（already done）
# ---------------------------------------------------------------------------
def test_already_done_is_idempotent_and_skipped():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        chars = [{
            "name": "审校",
            "offscreen": {"actions": [
                {"action": "已完成的事", "done": True, "_done_at_ch": 2},
            ]},
        }]
        executed = [{"character": "审校", "action_index": 0, "completed_fully": True}]
        proj, cards_path = _mk_project(tmp, characters=chars, executed=executed, ch=9)

        code = _run(proj, 9)
        cards = _load_cards(cards_path)
        act = cards["characters"][0]["offscreen"]["actions"][0]
        # 仍 done=true，章号未被改写成 9（不重盖）
        assert act["done"] is True
        assert act["_done_at_ch"] == 2
        # already done 进 skipped → 退出 1
        assert code == 1


# ---------------------------------------------------------------------------
# completed_fully=false 不标 done
# ---------------------------------------------------------------------------
def test_not_completed_fully_not_marked():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        chars = [{
            "name": "江条款",
            "offscreen": {"actions": [{"action": "做了一半", "done": False}]},
        }]
        executed = [{"character": "江条款", "action_index": 0, "completed_fully": False}]
        proj, cards_path = _mk_project(tmp, characters=chars, executed=executed, ch=3)

        code = _run(proj, 3)
        cards = _load_cards(cards_path)
        act = cards["characters"][0]["offscreen"]["actions"][0]
        assert act["done"] is False           # 未被标 done
        assert "_done_at_ch" not in act
        assert code == 1                       # 进 skipped


# ---------------------------------------------------------------------------
# 边界：缺 action_index / 越界 / 角色找不到 → 都跳过不崩，且不写坏文件
# ---------------------------------------------------------------------------
def test_edge_cases_skip_without_crash():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        chars = [{
            "id": "char_001",
            "name": "江条款",
            "offscreen": {"actions": [{"action": "唯一一条", "done": False}]},
        }]
        executed = [
            {"character": "江条款", "completed_fully": True},                 # 缺 action_index
            {"character": "江条款", "action_index": 99, "completed_fully": True},  # 越界
            {"character": "查无此人", "action_index": 0, "completed_fully": True},  # 角色找不到
        ]
        proj, cards_path = _mk_project(tmp, characters=chars, executed=executed, ch=7)

        code = _run(proj, 7)
        cards = _load_cards(cards_path)
        act = cards["characters"][0]["offscreen"]["actions"][0]
        # 唯一合法的 action 不在 executed 里 → 仍 false，文件结构完好
        assert act["done"] is False
        assert "_done_at_ch" not in act
        # 全是 skipped，无任何 update → 退出 1
        assert code == 1


# ---------------------------------------------------------------------------
# id 索引：用 character id（非 name）也能命中
# ---------------------------------------------------------------------------
def test_lookup_by_id_alias():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        chars = [{
            "id": "char_007",
            "name": "江条款",
            "offscreen": {"actions": [{"action": "用 id 命中", "done": False}]},
        }]
        executed = [{"character": "char_007", "action_index": 0, "completed_fully": True}]
        proj, cards_path = _mk_project(tmp, characters=chars, executed=executed, ch=4)

        code = _run(proj, 4)
        cards = _load_cards(cards_path)
        act = cards["characters"][0]["offscreen"]["actions"][0]
        assert act["done"] is True
        assert act["_done_at_ch"] == 4
        assert code == 0


# ---------------------------------------------------------------------------
# dry-run：计划不落盘，退出 0
# ---------------------------------------------------------------------------
def test_dry_run_does_not_write():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        chars = [{
            "name": "江条款",
            "offscreen": {"actions": [{"action": "应被计划但不写", "done": False}]},
        }]
        executed = [{"character": "江条款", "action_index": 0, "completed_fully": True}]
        proj, cards_path = _mk_project(tmp, characters=chars, executed=executed, ch=6)
        before = cards_path.read_text(encoding="utf-8")

        code = _run(proj, 6, dry_run=True)
        after = cards_path.read_text(encoding="utf-8")
        # dry-run 文件原封不动
        assert before == after
        assert json.loads(after)["characters"][0]["offscreen"]["actions"][0]["done"] is False
        assert code == 0


# ---------------------------------------------------------------------------
# 退出码契约：changes 缺失 / executed 空 / 人物卡损坏
# ---------------------------------------------------------------------------
def test_exit_code_contract():
    # (1) changes.json 不存在 → 0
    with tempfile.TemporaryDirectory() as d:
        proj, _ = _mk_project(Path(d), characters=[], write_changes=False, ch=1)
        assert _run(proj, 1) == 0

    # (2) offscreen_actions_executed 为空 → 0
    with tempfile.TemporaryDirectory() as d:
        proj, _ = _mk_project(Path(d), characters=[], executed=[], ch=1)
        assert _run(proj, 1) == 0

    # (3) 有 executed 但人物卡缺失 → 2（FATAL）
    with tempfile.TemporaryDirectory() as d:
        proj, cards_path = _mk_project(
            Path(d), characters=None,
            executed=[{"character": "X", "action_index": 0, "completed_fully": True}],
            ch=1)
        assert not cards_path.exists()
        assert _run(proj, 1) == 2

    # (4) 人物卡损坏 JSON → 2（load_json 返回 None → FATAL）
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj = tmp / "测试书"
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "人物卡.json").write_text("{broken", encoding="utf-8")
        ch_dir = proj / "章节" / "第001章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / "第001章_changes.json").write_text(
            json.dumps({"self_eval": {"offscreen_actions_executed": [
                {"character": "X", "action_index": 0, "completed_fully": True}]}},
                ensure_ascii=False),
            encoding="utf-8")
        assert _run(proj, 1) == 2
