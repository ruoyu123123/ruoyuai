"""character_index 回归测试 —— 守护角色出现历史索引的确定性逻辑。

被测脚本 core/scripts/character_index.py 全部为无 LLM / 无联网的纯逻辑：
  - load_json：JSON 读取 + 兜底
  - find_chapter_files：扫描 章节/第*章 目录 + 平铺旧布局，去重排序
  - scan_chapter_for_character：单角色单章正文扫描（提及/对话/首句）
  - build_index：聚合 人物卡.json + 各章正文，产出 first/last/total + 申报vs实际告警

约定：只用标准库；test_* 无参；tempfile 临时目录；utf-8。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import character_index as ci


# ============ 测试夹具：搭真项目布局（v18 嵌套 章节/第NNN章/第NNN章.txt）============

def _write_chapter(project_root: Path, ch: int, body: str):
    """按 v18 布局写一章纯正文，cio.read_body 能直接读到。"""
    d = project_root / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{ch:03d}章.txt").write_text(body, encoding="utf-8")


def _write_cards(project_root: Path, cards: list):
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": cards}, ensure_ascii=False), encoding="utf-8"
    )


# ============ load_json ============

def test_load_json_happy():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.json"
        p.write_text(json.dumps({"a": 1, "中": "文"}), encoding="utf-8")
        assert ci.load_json(p) == {"a": 1, "中": "文"}


def test_load_json_missing_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "nope.json"
        # 缺文件 → 返回传入的 default（不抛异常）
        assert ci.load_json(p, {"fallback": True}) == {"fallback": True}
        assert ci.load_json(p) is None


def test_load_json_malformed_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        # 损坏 JSON → 走 except 分支返回 default，绝不抛
        assert ci.load_json(p, {"safe": 1}) == {"safe": 1}


# ============ find_chapter_files ============

def test_find_chapter_files_nested_and_flat_sorted():
    """嵌套 章节/第NNN章 + 平铺 第N章.txt 都识别；去重 + 升序 + 返回 (ch, ch)。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 嵌套布局
        (tmp / "章节" / "第001章").mkdir(parents=True)
        (tmp / "章节" / "第003章").mkdir(parents=True)
        # 平铺旧布局
        (tmp / "第2章.txt").write_text("正文", encoding="utf-8")
        # changes.json 必须被排除（不算章节）
        (tmp / "第5章_changes.json").write_text("{}", encoding="utf-8")
        out = ci.find_chapter_files(tmp)
        assert out == [(1, 1), (2, 2), (3, 3)]


def test_find_chapter_files_empty():
    with tempfile.TemporaryDirectory() as d:
        assert ci.find_chapter_files(Path(d)) == []


def test_find_chapter_files_dedup_nested_vs_flat():
    """同一章同时存在嵌套目录与平铺 txt → 用 set 去重，只算一次。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "章节" / "第004章").mkdir(parents=True)
        (tmp / "第4章.txt").write_text("正文", encoding="utf-8")
        assert ci.find_chapter_files(tmp) == [(4, 4)]


# ============ scan_chapter_for_character ============

def test_scan_character_absent():
    """角色名不在正文 → 只返回 {'appears': False}，无其它字段。"""
    out = ci.scan_chapter_for_character("这一章里没有那个人。", "克莱")
    assert out == {"appears": False}


def test_scan_character_basic_mentions_and_dialogue():
    """角色出现：appears=True + mention_count + 紧邻引号对话 + 首句上下文。"""
    body = "克莱走进房间。克莱说“你来了吗”。窗外下着雨。"
    out = ci.scan_chapter_for_character(body, "克莱")
    assert out["appears"] is True
    # "克莱" 出现 2 次
    assert out["mention_count"] == 2
    # 紧邻引号（克莱说“...”）被提取
    assert "你来了吗" in out["dialogue_samples"]
    assert out["dialogue_count_estimate"] >= 1
    # 首句上下文从首次出现回溯到句首、向前到句号
    assert out["first_line_context"].startswith("克莱走进房间")


def test_scan_character_dialogue_fallback_path():
    """name 后无紧邻引号 → 退一步找 name 之后最近的引号内容（最多 3 条）。"""
    # "阿珂" 后面隔着很多非引号文字（>10）才出现引号 → 不走主匹配，走 fallback finditer
    body = "阿珂沉默了很久很久很久很久很久之后才缓缓开口“我不知道”。"
    out = ci.scan_chapter_for_character(body, "阿珂")
    assert out["appears"] is True
    # fallback 在 name 之后 200 字窗口内找到引号
    assert out["dialogue_samples"] == ["我不知道"]
    assert out["dialogue_count_estimate"] == 1


def test_scan_character_samples_capped_at_three():
    """对话样本上限 3 条（dialogue_samples[:3]），即便正文有更多。"""
    body = (
        "甲说“一”。甲说“二”。甲说“三”。甲说“四”。甲说“五”。"
    )
    out = ci.scan_chapter_for_character(body, "甲")
    assert out["appears"] is True
    assert len(out["dialogue_samples"]) == 3
    # 估计句数统计的是全部匹配（不被 [:3] 截断）
    assert out["dialogue_count_estimate"] == 5


def test_scan_character_first_line_context_no_period():
    """正文无句号时 first_line_context 退化为 首次出现起 100 字内（不崩）。"""
    body = "乙乙乙乙乙没有任何句号的一长串文字" * 2
    out = ci.scan_chapter_for_character(body, "乙")
    assert out["appears"] is True
    assert isinstance(out["first_line_context"], str)
    assert "乙" in out["first_line_context"]


# ============ build_index ============

def test_build_index_happy_path():
    """完整项目：人物卡 + 多章正文 → first/last/total/appearances 正确聚合。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_cards(tmp, [
            {"id": "c1", "name": "克莱", "role": "主角", "first_appearance_ch": 1},
        ])
        _write_chapter(tmp, 1, "克莱在第一章。克莱说“开始”。")
        _write_chapter(tmp, 2, "这一章没有他出现。")
        _write_chapter(tmp, 3, "克莱在第三章。克莱说“结束”。")
        idx = ci.build_index(tmp)
        assert idx["snapshot_at_ch"] == 3
        ch = idx["characters"]["克莱"]
        assert ch["card_id"] == "c1"
        assert ch["role"] == "主角"
        assert ch["first_appearance_ch_actual"] == 1
        assert ch["last_appearance_ch_actual"] == 3
        # ch1 + ch3 出现，ch2 缺席
        assert ch["total_chapters_appeared"] == 2
        assert [a["ch"] for a in ch["appearances"]] == [1, 3]
        # 申报与实际一致 → 无告警
        assert "declaration_warning" not in ch


def test_build_index_declaration_warning():
    """人物卡申报首章与实际首次出现不符 → 写入 declaration_warning。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_cards(tmp, [
            {"id": "c2", "name": "薇尔莉特", "first_appearance_ch": 1},
        ])
        # 申报 ch1 但实际只在 ch2 才出现
        _write_chapter(tmp, 1, "空无一人的开场。")
        _write_chapter(tmp, 2, "薇尔莉特终于登场了。")
        idx = ci.build_index(tmp)
        ch = idx["characters"]["薇尔莉特"]
        assert ch["first_appearance_ch_actual"] == 2
        assert "declaration_warning" in ch
        assert "ch2" in ch["declaration_warning"]


def test_build_index_empty_project():
    """无人物卡 + 无章节 → snapshot_at_ch=0，characters 为空，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        tmp.joinpath("_数据库").mkdir(parents=True)
        idx = ci.build_index(tmp)
        assert idx["snapshot_at_ch"] == 0
        assert idx["characters"] == {}


def test_build_index_card_never_appears():
    """登记角色但从未在任何章节出现 → total=0，first/last 仍为 None。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_cards(tmp, [{"id": "c3", "name": "幽灵角色"}])
        _write_chapter(tmp, 1, "正文里完全没有这个名字。")
        idx = ci.build_index(tmp)
        ch = idx["characters"]["幽灵角色"]
        assert ch["total_chapters_appeared"] == 0
        assert ch["first_appearance_ch_actual"] is None
        assert ch["last_appearance_ch_actual"] is None
        assert ch["appearances"] == []
        # 实际 None → 不触发申报告警
        assert "declaration_warning" not in ch
