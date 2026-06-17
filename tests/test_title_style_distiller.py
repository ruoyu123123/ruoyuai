"""title_style_distiller 回归测试 — 锁定章节标题命名风格蒸馏的确定性逻辑。

被测脚本是纯函数聚合器（无 LLM / 无联网）：分档 / 结构分类 / 噪声剥离 /
标点检测 / 标题收集（双 glob + 去重）/ 指纹聚合。这些测试钉死分支与边界。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import title_style_distiller as mod


def test_classify_tier_boundaries():
    """三档边界：<=4 normal / 5-8 mid / >8 high（对齐 80/15/5 三档）。"""
    assert mod.classify_tier(1) == "normal"
    assert mod.classify_tier(4) == "normal"   # 上界含 4
    assert mod.classify_tier(5) == "mid"      # 越界进 mid
    assert mod.classify_tier(8) == "mid"      # 上界含 8
    assert mod.classify_tier(9) == "high"     # 越界进 high
    assert mod.classify_tier(20) == "high"


def test_classify_structure_branches():
    """结构启发式各分支：编号/数字/引用/动作/人物/短语/名词。"""
    # 编号型：数字 + 破折号连接数字
    assert mod.classify_structure("研究3-0782") == "编号型"
    # 数字型：含数字但非编号
    assert mod.classify_structure("第三个秘密") == "数字型"
    # 引用型：含中文引号/双引号
    assert mod.classify_structure("「真相」") == "引用型"
    # 动作型：含动作字尾（如 杀/战/归）
    assert mod.classify_structure("出征") == "动作型"
    # 人物型：以称谓结尾（者/师/王/帝/大人）
    assert mod.classify_structure("引路者") == "人物型"
    # 短语型：含「的」「之」但无前面命中
    assert mod.classify_structure("命运的齿轮") == "短语型"
    # 名词型：纯名词兜底
    assert mod.classify_structure("孤山") == "名词型"


def test_clean_title_strips_noise():
    """剥离网文求票噪声副标题，返回 (clean, noise_tags)。"""
    clean, noise = mod.clean_title("终局之战（求月票）")
    assert clean == "终局之战"
    assert len(noise) == 1
    assert "求月票" in noise[0]

    # 无噪声时原样返回，noise 为空
    clean2, noise2 = mod.clean_title("孤山")
    assert clean2 == "孤山"
    assert noise2 == []

    # 多种噱声各自被识别（第N更 + 求推荐票）
    clean3, noise3 = mod.clean_title("血战（第三更）（求推荐票）")
    assert clean3 == "血战"
    assert len(noise3) == 2


def test_has_punctuation():
    """标点检测：含特殊标点返回 True，纯 CJK 返回 False。"""
    assert mod.has_punctuation("真相？") is True
    assert mod.has_punctuation("「引用」") is True
    assert mod.has_punctuation("天地——人") is True
    assert mod.has_punctuation("孤山落雪") is False


def _mk_project(tmp: Path, files: dict) -> Path:
    """在临时项目下建 蒸馏进度/ 目录并写入若干单章 JSON。files: {文件名: payload dict}。"""
    metrics = tmp / "蒸馏进度"
    metrics.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (metrics / name).write_text(json.dumps(payload, ensure_ascii=False),
                                    encoding="utf-8")
    return tmp


def test_collect_titles_dedup_and_skip():
    """双 glob 收集 + 同章去重 + 缺 title/chapter 跳过 + _metrics 排除。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "第1章.json": {"chapter": 1, "title": "孤山"},
            "ch1.json": {"chapter": 1, "title": "重复不该计两次"},  # 同章 → 去重
            "第2章.json": {"chapter": 2, "title": "血战（求月票）"},   # 噪声会被剥
            "第3章.json": {"chapter": 3, "title": ""},                # 空 title → 跳过
            "第4章.json": {"title": "缺章号"},                        # 缺 chapter → 跳过
            "ch5_metrics.json": {"chapter": 5, "title": "应被排除"},  # _metrics 不收
        })
        titles = mod.collect_titles(tmp)
        chapters = sorted(t["chapter"] for t in titles)
        assert chapters == [1, 2]              # 只剩 ch1（去重后取先到的）+ ch2
        ch2 = next(t for t in titles if t["chapter"] == 2)
        assert ch2["title_clean"] == "血战"     # 噪声剥离生效
        assert ch2["noise_tags"]                # 记录了噪声标签


def test_collect_titles_missing_dir_returns_empty():
    """蒸馏进度/ 不存在 → 返回空列表，不抛异常。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.collect_titles(Path(d)) == []


def test_aggregate_empty_returns_error():
    """空输入 → error 分支，不做除零。"""
    out = mod.aggregate_title_style([])
    assert out == {"error": "no titles found"}


def test_aggregate_full_fingerprint():
    """happy path：聚合产出长度统计 / tier 占比 / 结构占比 / 偏差 / 黄金示例。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "第1章.json": {"chapter": 1, "title": "孤山"},          # len2 normal 名词
            "第2章.json": {"chapter": 2, "title": "出征"},          # len2 normal 动作
            "第3章.json": {"chapter": 3, "title": "命运的齿轮转动"},  # len7 mid 短语
            "第4章.json": {"chapter": 4, "title": "天地之间众生皆为刍狗局"},  # len11 high
        })
        titles = mod.collect_titles(tmp)
        agg = mod.aggregate_title_style(titles)

        assert agg["total_titles"] == 4
        assert agg["schema_version"] == "v22.title.1"
        # 长度统计：min=2（孤山/出征），max=11
        assert agg["length_stats"]["min"] == 2
        assert agg["length_stats"]["max"] == 11
        # tier 占比：2 normal /1 mid /1 high
        assert agg["tier_distribution_pct"]["normal"] == 0.5
        assert agg["tier_distribution_pct"]["mid"] == 0.25
        assert agg["tier_distribution_pct"]["high"] == 0.25
        # 偏差 = 实际 - 默认（normal 0.5-0.8 = -0.3）
        dev = agg["tier_recommended_compared_to_default_80_15_5"]
        assert dev["normal_deviation"] == -0.3
        # 黄金示例 normal 档含两条短标题
        assert "孤山" in agg["golden_samples_per_tier"]["normal"]
        assert "出征" in agg["golden_samples_per_tier"]["normal"]
        # 偏差文档键存在
        assert "_doc" in agg


def test_aggregate_high_freq_chars_threshold():
    """高频字阈值：仅出现 >=3 次的 CJK 字才进 high_freq_chars。"""
    # 构造 4 个标题，「龙」出现 4 次（>=3 入选），「孤」只 1 次（不入选）
    titles = []
    for i, t in enumerate(["龙之战", "龙吟", "龙王归", "孤龙"], start=1):
        clean, noise = mod.clean_title(t)
        titles.append({
            "chapter": i,
            "title_raw": t,
            "title_clean": clean,
            "length": len(clean),
            "tier": mod.classify_tier(len(clean)),
            "structure": mod.classify_structure(clean),
            "has_punctuation": mod.has_punctuation(clean),
            "noise_tags": noise,
        })
    agg = mod.aggregate_title_style(titles)
    hf_chars = [c for c, n in agg["high_freq_chars"]]
    assert "龙" in hf_chars       # 4 次 >= 3 阈值
    assert "孤" not in hf_chars    # 1 次 < 3 阈值
