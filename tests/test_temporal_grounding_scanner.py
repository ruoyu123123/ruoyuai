#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_temporal_grounding_scanner.py — 时间流逝感缺失检测（advisory · 纯 stdlib）

覆盖：①active 触发 FAIL_MINOR ②干净 PASS ③shadow 不上报 ④<500 跳过 ⑤off 骨架。
合成草稿真命中（非空壳）。
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import temporal_grounding_scanner as tgs  # noqa: E402


@contextmanager
def _env(mode):
    """临时设置 TEMPORAL_GROUNDING_MODE·退出还原。"""
    key = "TEMPORAL_GROUNDING_MODE"
    old = os.environ.get(key)
    if mode is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = mode
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _write(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


# —— 合成草稿：4 个场景（2 空行分隔）· 全部缺时间推进锚点 → thin_ratio = 1.0 ——
_SCENE_THIN_1 = (
    "他握紧拳头盯着对面那张脸，空气里满是火药味，谁也不肯先开口。桌上的茶水早凉透，"
    "没人去碰。窗外有风刮动招牌，铁皮哗啦作响。他把那封信推到桌子中央，纸角卷起，"
    "墨迹在灯下泛着冷光，对面的人盯着信封始终没有伸手去拿。两个人就这么僵着，"
    "屋里安静得能听见彼此的呼吸。他忽然笑了一声，伸手按住信封，指节因为用力而发白，"
    "声音压得极低，说出口的每个字都像砸在对方心口的石头。"
)
_SCENE_THIN_2 = (
    "码头上人声嘈杂，货箱堆成小山，绳索勒进搬运工的肩膀。他混在人群里压低帽檐，"
    "目光扫过每一张脸。有人撞了他一下，骂骂咧咧地走开。他摸了摸怀里的硬物，心跳得厉害。"
    "前方那艘货船正缓缓靠岸，甲板上站着几个穿黑衣的人，腰间鼓鼓囊囊。他往人堆里又缩了缩，"
    "盯着那几个人卸下的木箱，箱缝里渗出暗红的痕迹，引来一群苍蝇嗡嗡打转，没有人敢上前。"
)
_SCENE_THIN_3 = (
    "审讯室没有窗，只有头顶一盏惨白的灯。铁椅冰凉，他被铐在上面动弹不得。门被推开，"
    "皮鞋声由远及近。来人把一叠照片甩在桌上，照片里是他从未见过的死者。你认识他吗。"
    "他摇头。对方冷笑一声，又抽出第二叠照片狠狠拍下。照片越铺越多，桌面被盖得严严实实，"
    "每一张都对准他的眼睛。他盯着其中一张，喉咙发紧，却始终咬死牙关一个字也不肯吐。"
)
_SCENE_THIN_4 = (
    "巷子很窄，两边的墙挤得人喘不上气。他贴着墙根快步走，鞋底踩到水洼溅起脏水。"
    "身后的脚步声始终不远不近地跟着。他猛地转身，巷子里空无一人，只有一只猫从垃圾桶后"
    "窜出去。冷汗顺着脊背往下淌，他握紧了袖子里的刀。前面是一堵死墙，墙头爬满枯藤，"
    "他踮脚去够砖缝，指甲抠得生疼，砖屑簌簌往下掉，身后的脚步声却越来越近，越来越重。"
)
DRAFT_THIN = "\n\n\n".join([_SCENE_THIN_1, _SCENE_THIN_2, _SCENE_THIN_3, _SCENE_THIN_4])

# —— 干净草稿：同结构·多数场景带时间推进锚点（傍晚/翌日/深夜）→ thin_ratio = 0.25 ——
DRAFT_CLEAN = "\n\n\n".join([
    "傍晚时分，" + _SCENE_THIN_1,
    "翌日，" + _SCENE_THIN_2,
    "深夜的" + _SCENE_THIN_3,
    _SCENE_THIN_4,
])

DRAFT_SHORT = "他握紧拳头盯着对面。空气里满是火药味。"


class TestTemporalGroundingScanner(unittest.TestCase):

    def test_active_triggers_fail_minor(self):
        """①active 模式·缺时间锚点的多场景草稿 → FAIL_MINOR + violation 真命中。"""
        path = _write(DRAFT_THIN)
        with _env("active"):
            rep = tgs.scan(path)
        self.assertEqual(rep["mode"], "active")
        self.assertEqual(rep["code"], "TEMPORAL_GROUNDING_THIN")
        self.assertEqual(rep["gate_level"], "advisory")
        self.assertGreaterEqual(rep["total_scenes"], 3)
        self.assertGreater(rep["thin_ratio"], 0.7)
        self.assertEqual(rep["verdict"], "FAIL_MINOR")
        self.assertIsNotNone(rep["warning"])
        self.assertEqual(rep["violations_count"], 1)
        self.assertEqual(rep["violations"][0]["kind"], "temporal_grounding_thin")
        self.assertEqual(rep["violations"][0]["severity"], "minor")

    def test_clean_passes(self):
        """②带时间锚点的草稿 → PASS·无 violation。"""
        path = _write(DRAFT_CLEAN)
        with _env("active"):
            rep = tgs.scan(path)
        self.assertLessEqual(rep["thin_ratio"], 0.7)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertIsNone(rep["warning"])
        self.assertEqual(rep["violations_count"], 0)

    def test_shadow_does_not_report(self):
        """③shadow 模式·同触发草稿 → 不 populate violations（零回归）。"""
        path = _write(DRAFT_THIN)
        with _env("shadow"):
            rep = tgs.scan(path)
        self.assertEqual(rep["mode"], "shadow")
        self.assertGreater(rep["thin_ratio"], 0.7)   # 度量仍算（证非空壳）
        self.assertEqual(rep["verdict"], "PASS")
        self.assertIsNone(rep["warning"])
        self.assertEqual(rep["violations"], [])
        self.assertEqual(rep["violations_count"], 0)

    def test_default_mode_is_active(self):
        """env 缺省 → 默认 active（2026-06-20 金标准放量·5真作者 thin_ratio 全 0 零误报）。"""
        path = _write(DRAFT_THIN)
        with _env(None):
            rep = tgs.scan(path)
        self.assertEqual(rep["mode"], "active")
        self.assertEqual(rep["verdict"], "FAIL_MINOR")

    def test_short_draft_skipped(self):
        """④<500 CJK → 跳过（note）·不判。"""
        path = _write(DRAFT_SHORT)
        with _env("active"):
            rep = tgs.scan(path)
        self.assertIn("note", rep)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["violations"], [])
        self.assertNotIn("metrics", rep)

    def test_off_returns_skeleton(self):
        """⑤off 模式 → 直返骨架·不读草稿不算度量。"""
        path = _write(DRAFT_THIN)
        with _env("off"):
            rep = tgs.scan(path)
        self.assertEqual(rep["mode"], "off")
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["violations"], [])
        self.assertNotIn("metrics", rep)
        self.assertNotIn("total_scenes", rep)

    def test_json_serializable(self):
        """报告可 JSON 序列化（audit_hub 消费契约）。"""
        path = _write(DRAFT_THIN)
        with _env("active"):
            rep = tgs.scan(path)
        s = json.dumps(rep, ensure_ascii=False)
        self.assertIn("temporal_grounding", s)


if __name__ == "__main__":
    unittest.main()
