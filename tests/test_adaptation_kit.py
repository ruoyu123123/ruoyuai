"""改编资料包生成器测试（2026-06-15·确定性·零依赖·喂 IP 后端）。

守护（北极星·纯确定性投影不编造）：
  1. build_character_bios 人物卡 → 人物小传（name/role/外貌/声纹/弧线/锁定事实）；
  2. build_worldbuilding 世界观 → 设定集（era/rules/factions/entries）；
  3. build_synopsis 进度volumes → 梗概；build_climax_hooks 伏笔表 → 高潮清单；
  4. 缺子系统 → 空（投影现有不编造·不崩）；generate_kit 只落盘有内容的份。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import adaptation_kit as ak  # noqa: E402


def _setup_project(td, **dbs):
    root = Path(td)
    dbdir = root / "_数据库"
    dbdir.mkdir(parents=True, exist_ok=True)
    for name, data in dbs.items():
        (dbdir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def test_character_bios():
    """人物卡 → 人物小传（name/role/外貌/性格/声纹/弧线/锁定事实全投影）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _setup_project(td, 人物卡={"characters": [
            {"id": "重黎", "name": "重黎", "role": "主角", "appearance": "眉心细缝",
             "personality": "沉静隐忍", "voice_pack": {"style": "苍凉", "style_samples": ["通道断了"]},
             "arc": "断天→火种", "locked_facts": ["通天血脉最后一人"]}]})
        md = ak.build_character_bios(root)
        assert "重黎" in md and "主角" in md and "眉心细缝" in md
        assert "苍凉" in md and "通道断了" in md
        assert "断天→火种" in md and "通天血脉最后一人" in md


def test_worldbuilding():
    """世界观 → 设定集（era/rules/factions/entries）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _setup_project(td, 世界观={
            "era": "上古神话", "location": "天梯", "rules": ["凿满七窍=末法"],
            "factions": ["立序派", "撬动派"],
            "entries": [{"title": "七窍说", "content": "世界=混沌"}]})
        md = ak.build_worldbuilding(root)
        assert "上古神话" in md and "天梯" in md and "凿满七窍=末法" in md
        assert "立序派" in md and "七窍说" in md and "世界=混沌" in md


def test_synopsis():
    """进度volumes → 故事梗概。"""
    with tempfile.TemporaryDirectory() as td:
        root = _setup_project(td, 进度={"volumes": [
            {"title": "第一卷·绝天纪", "core_conflict": "通天该不该断",
             "volume_arc": "断天→留缝", "ending_state": "凿穿第一窍"}]})
        md = ak.build_synopsis(root)
        assert "第一卷·绝天纪" in md and "通天该不该断" in md
        assert "断天→留缝" in md and "凿穿第一窍" in md


def test_climax_hooks():
    """伏笔表 → 高潮伏笔清单（tier/desc）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _setup_project(td, 伏笔表={"promises": [{"desc": "五色石身世", "tier": 1}],
                                          "secrets": []})
        md = ak.build_climax_hooks(root)
        assert "五色石身世" in md and "tier1" in md


def test_missing_subsystem_empty_no_crash():
    """缺子系统 → 空字符串（投影现有不编造·不崩）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "_数据库").mkdir(parents=True)
        assert ak.build_character_bios(root) == ""
        assert ak.build_worldbuilding(root) == ""
        assert ak.build_synopsis(root) == ""
        assert ak.build_climax_hooks(root) == ""


def test_generate_kit_writes_only_nonempty():
    """generate_kit 只落盘有内容的份（缺的跳过·不产空文件）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _setup_project(td,
            人物卡={"characters": [{"id": "A", "name": "A", "role": "主角", "arc": "x"}]},
            世界观={"era": "纪元X"})
        result = ak.generate_kit(root)
        written = result["written"]
        assert "人物小传.md" in written and "世界设定集.md" in written
        assert "故事梗概.md" not in written and "高潮伏笔清单.md" not in written
        assert (Path(result["out_dir"]) / "人物小传.md").exists()
