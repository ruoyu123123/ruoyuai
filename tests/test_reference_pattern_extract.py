#!/usr/bin/env python3
"""reference_pattern_extract 测试（P3·参考语料结构模式抽取·借鉴 Ex3-NovelWriter Extracting）。

验：
- 抽取确定性（同语料同输出·字节级）
- 版权纪律：artifact 任意字符串值 <50 字符 + 原文句子绝不泄漏进 artifact
- 无语料 / 无风格库 → 优雅 skip exit 0（不产物）
- 作者风格.json quantitative 量化指纹复用（仅数值·字符串全部剥掉）
- jobs 清单注入两态（artifact 存在 → volume_arc_jobs.json 携带「参考作品结构基线」块；缺失 → 空串）
- outline.plan.json 合法 + step5 条件脚本行（行首 ? ·口径同 style_injector）+ 引用脚本真实存在

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（pytest 同样可收集）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import reference_pattern_extract as rpe  # noqa: E402
import gen_creative_volume_arc as gva  # noqa: E402

STYLE_NAME = "测试风格"
BOOK_NAME = "测试书"
# 语料里一句有辨识度的原文（泄漏断言锚·≥10 字连续片段绝不许出现在 artifact 里）
_DISTINCT_SENTENCE = "钟楼的影子压向湿冷的街角石板"


def _make_chapter(no: int) -> str:
    """确定性合成章节：含对话（弯引号）、人名（attribution+敬称）、冲突词、转场标记、分隔线。"""
    paras = [
        f"第{no}夜，{_DISTINCT_SENTENCE}。",
        "林昭说道：“今晚的巡更换我来，你回去守灯。”",
        "沈无咎队长皱眉，指节敲着刀鞘，刀锋上还凝着血。",
        "他怒吼一声，撞开木门，杀意在雨里炸开。",
        "翌日，另一边的码头传来惨叫，人群四散而逃。",
        "※※※",
        "与此同时，雾里的钟声第三次响起，像有人在数着死期。",
    ]
    # 按章号追加不同数量的填充段（让各章 CJK / 指标有差异）
    for i in range(no % 4 + 1):
        paras.append(f"第{no}章第{i}段填充：巡更人踩着水洼往前走，风把灯火吹得东倒西歪。")
    return "\n".join(paras)


def _build_tree(root: Path, *, chapters: int = 6, with_profile: bool = True) -> Path:
    """建 workspace 树：styles/<风格>/原文/*.txt + novels/<书>/_数据库/.wal/style_choice.json。
    返回项目根（novels/<书>）。"""
    style_dir = root / "workspace" / "styles" / STYLE_NAME
    corpus = style_dir / "原文"
    corpus.mkdir(parents=True)
    for n in range(1, chapters + 1):
        (corpus / f"第{n:03d}章.txt").write_text(_make_chapter(n), encoding="utf-8")
    if with_profile:
        (style_dir / "作者风格.json").write_text(json.dumps({
            "source": STYLE_NAME,
            "quantitative": {
                "sentence_length": {"mean": 30.97, "std": 23.08, "_doc": "长短交错文字说明"},
                "dialogue_ratio": {"mean": 0.32},
                "directives": ["句长均值目标 34 字（这是文字约束·必须被剥掉）"],
                "signature_collocations": ["这不重要", "也就是说"],
            },
        }, ensure_ascii=False), encoding="utf-8")
    project = root / "workspace" / "novels" / BOOK_NAME
    wal = project / "_数据库" / ".wal"
    wal.mkdir(parents=True)
    (wal / "style_choice.json").write_text(json.dumps(
        {"answer": {"name": STYLE_NAME}}, ensure_ascii=False), encoding="utf-8")
    return project


def _artifact_path(project: Path) -> Path:
    return project.parent.parent / "styles" / STYLE_NAME / rpe.ARTIFACT_NAME


# ============ 1. 抽取确定性（同语料同输出）============
def test_extraction_deterministic():
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td))
        assert rpe.main([str(project)]) == 0
        art = _artifact_path(project)
        assert art.is_file(), "artifact 未落盘"
        first = art.read_bytes()
        # --force 绕过语料签名幂等缓存·强制重算 → 必须字节级一致
        assert rpe.main([str(project), "--force"]) == 0
        assert art.read_bytes() == first, "同语料重算输出不一致（确定性破损）"
        data = json.loads(first.decode("utf-8"))
        assert data["_schema"] == "genre_storyline_patterns"
        assert data["source"]["chapter_count"] == 6
        assert len(data["source_ids"]) == 6
        dims = data["dimensions"]
        for key in ("chapter_cjk", "dialogue_ratio", "scene_shift_per_1k",
                    "conflict_per_1k", "new_entities_per_chapter", "volume_pacing_shape"):
            assert key in dims, f"缺维度 {key}"
            assert dims[key].get("provenance"), f"{key} 缺 provenance"
        # 语料确有对话/冲突/转场 → 统计不该全 0
        assert dims["chapter_cjk"]["stats"]["mean"] > 0
        assert dims["dialogue_ratio"]["stats"]["mean"] > 0
        assert dims["conflict_per_1k"]["stats"]["mean"] > 0
        assert dims["scene_shift_per_1k"]["stats"]["mean"] > 0
        segs = dims["volume_pacing_shape"]["segments"]
        assert set(segs) == {"front", "mid", "back"}


# ============ 2. 版权纪律：无原文句子泄漏 + 字符串值 <50 字符 ============
def _walk_values(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_values(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_values(v, f"{path}[{i}]")
    else:
        yield path, obj


def test_artifact_no_raw_text_leak():
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td))
        assert rpe.main([str(project)]) == 0
        art = _artifact_path(project)
        raw = art.read_text(encoding="utf-8")
        # 原文句子（连续 ≥10 字片段）绝不许出现
        assert _DISTINCT_SENTENCE not in raw, "artifact 泄漏了原文句子"
        assert "巡更换我来" not in raw, "artifact 泄漏了原文对话"
        # 任意值：要么是数字/布尔/None，要么是 <50 字符的短标签
        for path, v in _walk_values(json.loads(raw)):
            if isinstance(v, str):
                assert len(v) < 50, f"{path} 字符串值过长（{len(v)} 字符）: {v[:60]}"
            else:
                assert v is None or isinstance(v, (int, float, bool)), \
                    f"{path} 非法叶子类型: {type(v).__name__}"


# ============ 3. 无语料优雅 skip（exit 0·不产物）============
def test_graceful_skip_without_corpus():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 3a. 项目根本没选风格库（无 style_choice / 无作者风格.json）
        bare = root / "workspace" / "novels" / "裸项目"
        (bare / "_数据库").mkdir(parents=True)
        assert rpe.main([str(bare)]) == 0
        # 3b. 有风格库但 原文/ 为空目录
        project = _build_tree(root, chapters=0)
        assert rpe.main([str(project)]) == 0
        assert not _artifact_path(project).exists(), "无语料不该产 artifact"
        # 消费端同态：无 artifact → 注入块必须为空串
        assert rpe.build_reference_patterns_block(project) == ""


# ============ 4. 作者档量化指纹复用（仅数值·字符串全剥）============
def test_author_profile_fingerprint_reuse_numeric_only():
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td), with_profile=True)
        assert rpe.main([str(project)]) == 0
        data = json.loads(_artifact_path(project).read_text(encoding="utf-8"))
        fp = data.get("author_profile_fingerprint")
        assert isinstance(fp, dict), "作者档有 quantitative 却没复用进 artifact"
        q = fp["quantitative"]
        assert q["sentence_length"]["mean"] == 30.97, "数值指纹没原样复用"
        assert q["dialogue_ratio"]["mean"] == 0.32
        # 文字约束/签名搭配/_doc 一律剥掉（版权+泄漏守卫）
        for path, v in _walk_values(q):
            assert not isinstance(v, str), f"指纹复用泄漏字符串 {path}: {v}"
        assert "directives" not in q and "signature_collocations" not in q


def test_fingerprint_absent_when_no_profile():
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td), with_profile=False)
        # 无 作者风格.json → 风格名走 style_choice.json 仍可解析·指纹段整体缺席
        assert rpe.main([str(project)]) == 0
        data = json.loads(_artifact_path(project).read_text(encoding="utf-8"))
        assert "author_profile_fingerprint" not in data


# ============ 5. jobs 清单注入两态（消费端 = volume_arc_jobs.json 的 reference_patterns_block）============
def _run_volume_arc_pending(project: Path) -> dict:
    """跑 volume_arc（骨架单元缺失 → exit 2=pending）→ 返回任务清单 dict。"""
    import types
    card = project / "_数据库" / ".wal" / "card.json"
    card.write_text(json.dumps({"answer": {"title": "钟楼"}}, ensure_ascii=False),
                    encoding="utf-8")
    args = types.SimpleNamespace(
        project=str(project), selected_card=str(card), cluster_count=8,
        framework="三幕", rhythm="标准", style_ref=None, research=None,
        emit_to_db=True)
    rc = gva._run_volume_arc(args)
    assert rc == 2, f"骨架单元缺失应 exit 2=pending，实得 {rc}"
    return json.loads((project / "_数据库" / ".wal" / gva.VOLUME_ARC_JOBS_WAL)
                      .read_text(encoding="utf-8"))


def test_jobs_manifest_carries_block_when_present():
    """artifact 存在 → volume_arc 任务清单携带 reference_patterns_block（agent 读它当
    advisory 结构参照·非硬约束）。"""
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td))
        assert rpe.main([str(project)]) == 0
        block = rpe.build_reference_patterns_block(project)
        assert block, "artifact 存在却产不出注入块"
        assert "章均CJK" in block and "冲突节奏" in block
        # 注入块本身也不许携带原文
        assert _DISTINCT_SENTENCE not in block
        manifest = _run_volume_arc_pending(project)
        assert manifest["reference_patterns_block"] == block, \
            "任务清单未携带参考结构基线块（agent 消费入口断了）"


def test_jobs_manifest_empty_block_when_absent():
    """无 artifact → 任务清单 reference_patterns_block 为空串（agent 不注入基线）。"""
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td), chapters=0)   # 无语料 → 无 artifact
        assert rpe.main([str(project)]) == 0
        manifest = _run_volume_arc_pending(project)
        assert manifest["reference_patterns_block"] == "", "无 artifact 时基线块应为空串"


# ============ 6. plan JSON 合法 + step5 条件脚本口径 ============
def test_outline_plan_has_conditional_extract_line():
    plan_path = _ROOT / "core" / "claude-home" / "plans" / "outline.plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))   # JSON 坏 = 契约破损直接抛
    step5 = next(s for s in plan["steps"] if s.get("n") == 5)
    scripts = step5.get("scripts") or []
    cond = [s for s in scripts if s.startswith("? ")
            and "core/scripts/reference_pattern_extract.py" in s]
    assert cond, f"step5 缺条件抽取脚本行（行首 ? 口径）: {scripts}"
    assert "{project_root}" in cond[0]
    # 条件脚本必须排在 volume_arc 生成之前（消费端在 volume_arc 里读 artifact）
    assert scripts.index(cond[0]) < next(
        i for i, s in enumerate(scripts) if "--mode volume_arc" in s)
    # 引用脚本真实存在（防幽灵引用）
    assert (_ROOT / "core" / "scripts" / "reference_pattern_extract.py").is_file()
    # 条件产物不进 hard expected_outputs（同 style_injector 口径）
    assert not any("genre_storyline_patterns" in o
                   for o in (step5.get("expected_outputs") or []))
    # required 主链口径未被破坏
    assert step5.get("required") is True
    assert 5 in plan["required_steps"]


# ============ 7. 幂等：语料签名未变 → 复用不重算 ============
def test_signature_reuse_skips_recompute():
    with tempfile.TemporaryDirectory() as td:
        project = _build_tree(Path(td))
        assert rpe.main([str(project)]) == 0
        art = _artifact_path(project)
        mtime = art.stat().st_mtime_ns
        content = art.read_bytes()
        # 二跑（无 --force·签名未变）：不重写文件
        assert rpe.main([str(project)]) == 0
        assert art.stat().st_mtime_ns == mtime, "签名未变却重写了 artifact"
        assert art.read_bytes() == content


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
