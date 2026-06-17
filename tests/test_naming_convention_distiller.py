"""naming_convention_distiller 回归测试 — 角色命名指纹蒸馏器（纯函数 · 零 LLM · 零联网）。

钉死命名规范蒸馏的确定性行为：归一化噪声剥离 / 文化倾向判定 / 姓氏与称谓检测 /
标签名过滤 / 别名归并 / 聚合指纹 + 网文化指数钳位 + 文件收集（character_arcs 优先·衔接分析 fallback）。

被测脚本全部为确定性逻辑（正则解析 / 集合查表 / Counter 聚合 / 文件读写），
不调用任何 LLM、不联网，适合纯单元测试锁行为。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import naming_convention_distiller as ncd  # noqa: E402


# ── normalize_name：噪声剥离 ──────────────────────────────────────────────
def test_normalize_name_strips_noise():
    # 全角括号 + 内容整体剥除
    assert ncd.normalize_name("萧炎（火）") == "萧炎"
    # 半角括号备注剥除
    assert ncd.normalize_name("林动[斗气]") == "林动"
    # 一名多写：取 / 前第一个
    assert ncd.normalize_name("HeroC/CharC") == "HeroC"
    assert ncd.normalize_name("张三，李四") == "张三"
    # 首尾空白与分隔标点剥除
    assert ncd.normalize_name("  -林动-  ") == "林动"
    # 空输入 → 空串（不崩）
    assert ncd.normalize_name("") == ""
    assert ncd.normalize_name(None) == ""


# ── detect_culture：文化倾向判定的各分支 ──────────────────────────────────
def test_detect_culture_branches():
    # 首字为常见汉姓 → chinese
    assert ncd.detect_culture("萧炎") == "chinese"
    # 非姓氏纯中文短名 → fallback chinese（古风名）
    assert ncd.detect_culture("子衿") == "chinese"
    # 纯英文 → western
    assert ncd.detect_culture("Hero") == "western"
    # 含分隔符（中点）→ western_translit
    assert ncd.detect_culture("HeroC·CharC") == "western_translit"
    # 含数字 → scifi（注：纯英文+数字会先命中 western 分支）
    assert ncd.detect_culture("赤铁3号") == "scifi"
    # 称号词缀的中文短名 → fantasy
    assert ncd.detect_culture("魔尊") == "fantasy"
    # 6+ 字纯中文 → fantasy（仿西/奇幻）
    assert ncd.detect_culture("阿斯顿马丁洛克") == "fantasy"
    # 空 → unknown
    assert ncd.detect_culture("") == "unknown"


# ── detect_surname / detect_title_suffix ─────────────────────────────────
def test_detect_surname_and_title_suffix():
    # 常见单字姓
    assert ncd.detect_surname("萧炎") == "萧"
    assert ncd.detect_surname("林冲") == "林"
    # 首字不在常见姓氏表（欧 不在表中）→ None（复姓分支仅在首字命中后才进入）
    assert ncd.detect_surname("欧阳锋") is None
    # 英文名无中文姓 → None
    assert ncd.detect_surname("Hero") is None
    assert ncd.detect_surname("") is None
    # 称谓后缀
    assert ncd.detect_title_suffix("李大人") == "大人"
    assert ncd.detect_title_suffix("赵队长") == "队长"
    assert ncd.detect_title_suffix("萧炎") is None


# ── _is_label_name：标签型名过滤 ─────────────────────────────────────────
def test_is_label_name_filters_non_persons():
    # 真实人名 → 不是标签
    assert ncd._is_label_name("萧炎") is False
    # 群像 / 小队 等标签模式 → 是标签
    assert ncd._is_label_name("群像小队") is True
    assert ncd._is_label_name("玩家阿强") is True
    # 括号/引号开头 → 标签
    assert ncd._is_label_name("【主角】") is True
    # 超长（>15）→ 标签
    assert ncd._is_label_name("一二三四五六七八九十一二三四五六") is True
    # 空 → 标签
    assert ncd._is_label_name("") is True


# ── dedupe_aliases：别名归并（最长者作 canonical）─────────────────────────
def test_dedupe_aliases_collapses_same_prefix():
    canonical, alias_map = ncd.dedupe_aliases(["HeroC", "HeroC·CharC5", "HeroC_Full", "萧炎"])
    # 萧炎 独立 canonical
    assert "萧炎" in canonical
    # 含分隔符的变体（short=HeroC ≠ 全名）才被收编为别名：
    # 最长者 HeroC·CharC5 先入 canonical 占住 short=HeroC；
    # HeroC_Full（short=HeroC ≠ 自身）→ 收编为 HeroC·CharC5 的别名。
    assert "HeroC·CharC5" in canonical
    assert alias_map.get("HeroC·CharC5") == ["HeroC_Full"]
    # 关键边界：裸名 HeroC 的 short==自身（short != name 为假）→ 不被收编，
    # 仍是独立 canonical（锁住 "short != name" 这条分支条件）。
    assert "HeroC" in canonical

    # 无别名场景：互不相干的名字全部成为 canonical
    canon2, amap2 = ncd.dedupe_aliases(["张三", "李四", "王五"])
    assert sorted(canon2) == ["张三", "李四", "王五"]
    assert amap2 == {}


# ── aggregate_naming_convention：聚合指纹 + 边界 ──────────────────────────
def test_aggregate_empty_returns_error():
    assert ncd.aggregate_naming_convention([]) == {"error": "no character names found"}


def test_aggregate_happy_path_fingerprint():
    names = ["萧炎", "林动", "Hero", "HeroC·CharC5"]
    res = ncd.aggregate_naming_convention(names)
    assert res["schema_version"] == "v22.naming.2"
    assert res["total_unique_characters"] == 4  # 4 个互不重复
    # 中文占多数 → primary_culture == chinese
    assert res["primary_culture"] == "chinese"
    # 文化分布是百分比 dict 且各值 ≤ 1
    dist = res["culture_distribution_pct"]
    assert "chinese" in dist
    assert all(0 <= v <= 1 for v in dist.values())
    # 中文姓氏 top10 含萧 与 林
    surnames = dict(res["chinese_surname_top10"])
    assert surnames.get("萧") == 1
    assert surnames.get("林") == 1
    # 含中点分隔符的名字记入西式分隔符使用率（HeroC·CharC5 占 1/4）
    assert res["western_separator_usage_pct"] == round(1 / 4, 3)
    # metadata 记录去重前后计数
    meta = res["_metadata"]
    assert meta["raw_name_count_before_dedup"] == 4
    assert meta["canonical_count_after_dedup"] == 4


def test_aggregate_web_novel_index_clamp_no_div_zero():
    """单名也不能除零崩溃（max(n,1) 钳位）+ 网文大姓命中拉高指数。"""
    # 萧 是网文大姓 → 命中 → index > 0
    res = ncd.aggregate_naming_convention(["萧炎"])
    wn = res["web_novel_indicators_round1d_applied"]
    assert wn["web_novel_surname_hits"] == 1
    assert wn["web_novel_index"] > 0
    assert wn["web_novel_index_tier"] in ("高度网文化", "中等网文化", "低/无网文化")
    # tier 与阈值一致：index 1.0 > 0.5 → 高度网文化
    if wn["web_novel_index"] > 0.5:
        assert wn["web_novel_index_tier"] == "高度网文化"


# ── collect_character_names：文件收集（character_arcs 优先 + fallback）────
def test_collect_from_character_arcs_filters_labels():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "book"
        ca = proj / "character_arcs"
        ca.mkdir(parents=True)
        (ca / "萧炎_emotion_arc.json").write_text("{}", encoding="utf-8")
        (ca / "林动_emotion_arc.json").write_text("{}", encoding="utf-8")
        (ca / "群像小队_emotion_arc.json").write_text("{}", encoding="utf-8")  # 标签 → 被过滤
        names = ncd.collect_character_names(proj)
        assert names == ["林动", "萧炎"]  # 排序 + 标签剔除


def test_collect_fallback_to_continuity_and_missing_dirs():
    # ② character_arcs 不存在 → fallback 到 衔接分析，兼容 dict/str/脏数据
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "book"
        c = proj / "衔接分析"
        c.mkdir(parents=True)
        (c / "x_continuity.json").write_text(
            json.dumps({"character_continuity": [
                {"character": "萧炎"},
                "林动",
                {"character": "群像小队"},  # 标签 → 过滤
                123,                          # 非 dict/str → 跳过不崩
            ]}, ensure_ascii=False),
            encoding="utf-8")
        names = ncd.collect_character_names(proj)
        assert names == ["林动", "萧炎"]

    # ③ 两个目录都不存在 → 返回空列表（不崩）
    with tempfile.TemporaryDirectory() as d:
        assert ncd.collect_character_names(Path(d) / "nope") == []
