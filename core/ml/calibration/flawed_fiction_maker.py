#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-07 S5 FlawedFictions 反向校准（测漏报）· 二轮移植
"""flawed_fiction_maker.py — 受控反事实注入器（FlawedFictions arXiv:2504.11900 镜像）。

【为什么】金标准校准（真作者原文喂 scanner）只测**误报**；一致性栈的**漏报率**从未量化。
本脚本把「真 cluster 草稿 + locked_facts」变成一组带 ground_truth 标注的受控穿帮样本，
供 flawed_fiction_runner.py 喂给一致性栈的可脚本化层测召回——得到检测能力地图。

【数据面】真草稿 = workspace/novels/主神验尸官 cluster_001（真 gen-model 产出散文）。
该项目人物卡 locked_facts 为空 → locked_facts 用**手造 fixture**（全部与原草稿内容相容，
保证 baseline 零冲突；实地核对过原文：秦烬能辨色/能听声/夜班法医，fixture 刻意避开
这些已占用维度）。每个样本 = 原草稿 + **恰好一处**注入，自带独立 fixture 项目
（_数据库/人物卡.json + 事件簇.json(linear) + 地图.json），互不串扰。

【五类注入】（任务规定 ①数值 ②直接改写 ③多跳 三类为核心；temporal/spatial 为
补充类——用于点火一致性栈第二层的 draft_temporal_order / spatial_continuity）：
  numeric           改/插锁定数值（N岁）→ 靶 locked_fact 恒定数值通路（hard_gate）
  direct_rewrite    直接否定一条锁定描述 → 靶 locked_fact NLI 描述类通路（advisory）
  multi_hop         间接违背锁定事实的场景片段（手工模板 4 个·防 LLM 依赖）
                    → 靶 Claude judge 层（reflector 维度9）；NLI 已知恒漏（110M 能力边界）
  temporal_reversal 无标记时间线倒退 → 靶 draft_temporal_order_scanner
  spatial_teleport  同场景无移动动词位置瞬移 → 靶 spatial_continuity_scanner

另含 2 个**已知边界探针**（expected_deterministic_detect=False·验证漏报根因可复现）：
  N3 跨句数值（数值与人名不同小句——同句锚定边界）
  D4 晚位注入（超出 _MAX_SENTS_PER_FACT=16 截断窗——NLI 配对截断边界）

【ground_truth 契约】每样本 ground_truth.json：
  sample_id / flaw_type / character / violated_fact(temporal/spatial 为 null) /
  injection{paragraph_index, char_offset, injected_text} / expected_layer /
  expected_deterministic_detect / expect{...匹配锚} / base_draft_sha256 / notes

【确定性】默认 template 模式纯确定性：同输入草稿两次运行逐字节一致（无随机源）。
LLM 增广模式（--mode llm）env 门控 RUOYU_RUN_REAL_API=1（真 API 测试纪律），
未开门控直接响亮失败 exit 2——绝不静默降级回模板。

用法：
  py core/ml/calibration/flawed_fiction_maker.py --out-dir <dir>
     [--draft <path>] [--mode template|llm]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_DRAFT = (_REPO / "workspace" / "novels" / "主神验尸官" / "章节"
                  / "cluster_001_draft" / "cluster_001_draft.txt")

PROTAGONIST = "秦烬"
PARA_SEP = "\n\n"
REAL_API_ENV = "RUOYU_RUN_REAL_API"

# ───────────────────── 手造 locked_facts fixture（与原草稿相容·baseline 零冲突）─────────────────────
# key → fact 文本。数值类只用「岁」（_DEFAULT_INVARIANT_UNITS 保底单位·原草稿无「岁」字）。
FACTS: dict[str, str] = {
    "age": f"{PROTAGONIST}二十九岁",
    "unmarried": f"{PROTAGONIST}未婚，没有妻子",
    "partner_dead": f"{PROTAGONIST}的搭档老周三年前殉职，已经死了",
    "is_medic": f"{PROTAGONIST}是市局法医鉴定中心的法医",
    "family_dead": f"{PROTAGONIST}全家死于二十年前的火灾，只剩他一个人活着",
    "alcohol_allergy": f"{PROTAGONIST}对酒精严重过敏，滴酒不沾",
    "never_south": f"{PROTAGONIST}从未去过南方，一辈子没离开过北方",
    "four_fingers": f"{PROTAGONIST}的右手小指在实习时被锯骨机切断，右手只有四根手指",
}

# fixture 地点词典（spatial_continuity 走 project 词典路径·词间无子串包含）
MAP_LOCATIONS = ["义庄大厅", "乱葬岗", "钟楼顶层"]

# ───────────────────── 注入 spec（全手工模板·确定性）─────────────────────
# paragraph_index 依据实测：原草稿 885 段、第 16 个含主角名的小句落在段 idx≈115 →
# NLI 靶样本一律注入 idx<110（保证进 _MAX_SENTS_PER_FACT=16 截断窗）；
# D4 刻意注入 idx=700（超窗·截断边界探针）。
FLAW_SPECS: list[dict] = [
    {
        "sample_id": "N1_numeric_age_cn",
        "flaw_type": "numeric",
        "fact_key": "age",
        "paragraph_index": 60,
        "injected_paragraphs": [f"{PROTAGONIST}今年三十五岁，档案上写得清清楚楚。"],
        "expected_layer": "locked_fact_numeric",
        "expected_deterministic_detect": True,
        "expect": {"kind": "numeric_conflict", "conflict_value": "三十五岁"},
        "notes": "中文数字·同句人名+数值+岁 → 恒定数值通路应必中（hard_gate）",
    },
    {
        "sample_id": "N2_numeric_age_arabic",
        "flaw_type": "numeric",
        "fact_key": "age",
        "paragraph_index": 80,
        "injected_paragraphs": [f"档案照片下面一行小字：{PROTAGONIST}，42岁，市局法医。"],
        "expected_layer": "locked_fact_numeric",
        "expected_deterministic_detect": True,
        "expect": {"kind": "numeric_conflict", "conflict_value": "42岁"},
        "notes": "阿拉伯数字变体·同句 → 应必中",
    },
    {
        "sample_id": "N3_numeric_cross_sentence_probe",
        "flaw_type": "numeric",
        "fact_key": "age",
        "paragraph_index": 90,
        "injected_paragraphs": [f"{PROTAGONIST}翻开自己的档案。第一页写着：三十五岁，市局法医。"],
        "expected_layer": "locked_fact_numeric",
        "expected_deterministic_detect": False,
        "expect": {"kind": "numeric_conflict", "conflict_value": "三十五岁"},
        "notes": "已知边界探针：数值与人名被「。」隔开不同小句 → 同句锚定设计性漏报",
    },
    {
        "sample_id": "D1_direct_spouse",
        "flaw_type": "direct_rewrite",
        "fact_key": "unmarried",
        "paragraph_index": 50,
        "injected_paragraphs": [f"{PROTAGONIST}的妻子就站在停尸房门口，等他下班一起回家。"],
        "expected_layer": "locked_fact_nli",
        "expected_deterministic_detect": True,
        "expect": {"kind": "nli_contradiction"},
        "notes": "直接否定「未婚无妻」·docstring 声称此类 contradiction 0.99+ 稳判",
    },
    {
        "sample_id": "D2_direct_partner_alive",
        "flaw_type": "direct_rewrite",
        "fact_key": "partner_dead",
        "paragraph_index": 70,
        "injected_paragraphs": [f"{PROTAGONIST}拍了拍老周的肩膀，老周活得好好的，还冲他咧嘴一笑。"],
        "expected_layer": "locked_fact_nli",
        "expected_deterministic_detect": True,
        "expect": {"kind": "nli_contradiction"},
        "notes": "「已死」vs「活得好好的」直接互斥",
    },
    {
        "sample_id": "D3_direct_profession",
        "flaw_type": "direct_rewrite",
        "fact_key": "is_medic",
        "paragraph_index": 100,
        "injected_paragraphs": [f"{PROTAGONIST}这辈子从没学过医，连一天法医都没有当过。"],
        "expected_layer": "locked_fact_nli",
        "expected_deterministic_detect": True,
        "expect": {"kind": "nli_contradiction"},
        "notes": "职业直接否定",
    },
    {
        "sample_id": "D4_direct_late_truncation_probe",
        "flaw_type": "direct_rewrite",
        "fact_key": "unmarried",
        "paragraph_index": 700,
        "injected_paragraphs": [f"{PROTAGONIST}的妻子就站在停尸房门口，等他下班一起回家。"],
        "expected_layer": "locked_fact_nli",
        "expected_deterministic_detect": False,
        "expect": {"kind": "nli_contradiction"},
        "notes": "已知边界探针：同 D1 文本注入段 idx=700（约第 100+ 个人名小句）→ "
                 "超出 _MAX_SENTS_PER_FACT=16 配对窗 → NLI 根本看不到该句（截断漏报）",
    },
    {
        "sample_id": "M1_multihop_family",
        "flaw_type": "multi_hop",
        "fact_key": "family_dead",
        "paragraph_index": 55,
        "injected_paragraphs": [
            f"除夕夜的团圆饭桌上，{PROTAGONIST}的哥哥隔着圆桌给他夹了一筷子鱼，笑骂他这一年又瘦了。"],
        "expected_layer": "judge_claude",
        "expected_deterministic_detect": False,
        "expect": {"kind": "nli_contradiction"},
        "notes": "多跳：哥哥∈全家 且 活着 → 违背「全家死绝只剩一人」·需实体归属推理",
    },
    {
        "sample_id": "M2_multihop_alcohol",
        "flaw_type": "multi_hop",
        "fact_key": "alcohol_allergy",
        "paragraph_index": 65,
        "injected_paragraphs": [
            f"庆功宴上，{PROTAGONIST}仰头灌下半瓶二锅头，抹了把嘴，面不改色地又拧开了第二瓶。"],
        "expected_layer": "judge_claude",
        "expected_deterministic_detect": False,
        "expect": {"kind": "nli_contradiction"},
        "notes": "多跳：二锅头=酒 → 酒精过敏者不可能面不改色连饮·需常识链",
    },
    {
        "sample_id": "M3_multihop_south",
        "flaw_type": "multi_hop",
        "fact_key": "never_south",
        "paragraph_index": 75,
        "injected_paragraphs": [
            f"{PROTAGONIST}熟门熟路地拐进广州的老巷，用一口流利的粤语跟肠粉店老板娘打了个招呼，"
            f"就像二十年前常来时那样。"],
        "expected_layer": "judge_claude",
        "expected_deterministic_detect": False,
        "expect": {"kind": "nli_contradiction"},
        "notes": "多跳：广州∈南方 + 常来 → 违背「从未去过南方」·需地理知识",
    },
    {
        "sample_id": "M4_multihop_fingers",
        "flaw_type": "multi_hop",
        "fact_key": "four_fingers",
        "paragraph_index": 85,
        "injected_paragraphs": [
            f"{PROTAGONIST}十指扣住井沿，双手整整齐齐十根手指，指节因为用力而发白。"],
        "expected_layer": "judge_claude",
        "expected_deterministic_detect": False,
        "expect": {"kind": "nli_contradiction"},
        "notes": "多跳：十根手指 → 右手 5 根 → 违背「右手只有四根」·需算术+身体部位推理",
    },
    {
        "sample_id": "T1_temporal_abs_reversal",
        "flaw_type": "temporal_reversal",
        "fact_key": None,
        "paragraph_index": 200,
        "injected_paragraphs": [
            f"第三天清晨，雾气散了些，{PROTAGONIST}蹲在井台边啃干粮。",
            "第六天，村口的雾墙忽然褪成了灰白色。",
            f"第四天夜里，{PROTAGONIST}又摸回了水井旁。",
        ],
        "expected_layer": "temporal_order",
        "expected_deterministic_detect": True,
        "expect": {"kind": "temporal_reversal", "reversal_anchor": "第四天"},
        "notes": "绝对天数倒退（第六天→第四天）·无闪回标志·fixture 声明 linear",
    },
    {
        "sample_id": "T2_temporal_tod_reversal",
        "flaw_type": "temporal_reversal",
        "fact_key": None,
        "paragraph_index": 300,
        "injected_paragraphs": [
            "清晨，雾还没散透，井台上凝着一层白霜。",
            f"黄昏时分，{PROTAGONIST}把最后一块尸斑记录进检验单。",
            f"上午的日头晒得井台发白，{PROTAGONIST}眯起了眼睛。",
        ],
        "expected_layer": "temporal_order",
        "expected_deterministic_detect": True,
        "expect": {"kind": "temporal_reversal", "reversal_anchor": "上午"},
        "notes": "同日时段倒退（黄昏→上午）·相邻场景无跨日锚",
    },
    {
        "sample_id": "S1_spatial_teleport_chain",
        "flaw_type": "spatial_teleport",
        "fact_key": None,
        "paragraph_index": 400,
        "injected_paragraphs": [
            f"{PROTAGONIST}站在义庄大厅正中，指尖笃笃地敲着棺木。",
            f"{PROTAGONIST}蹲在乱葬岗的一块断碑旁，扒了扒碑底的浮土。",
            f"{PROTAGONIST}趴在钟楼顶层的栏杆边，慢条斯理地数着底下的人影。",
        ],
        "expected_layer": "spatial_continuity",
        "expected_deterministic_detect": True,
        "expect": {"kind": "spatial_teleport",
                   "teleport_pairs": [["义庄大厅", "乱葬岗"], ["乱葬岗", "钟楼顶层"]]},
        "notes": "同场景三连跳（无移动动词/无转场标志）→ 2 个候选对=恰好过噪声地板(≥2)",
    },
]

BASELINE_ID = "B0_baseline_clean"


# ───────────────────── 构建 ─────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _character_card(target_fact_key: str | None) -> dict:
    """fixture 人物卡：目标 fact 排第一（防 NLI 64 对全局截断把靶 fact 饿死——
    _MAX_NLI_PAIRS=64 / 单 fact 16 对，8 条 fact 顺序遍历时第 5 条起拿不到配对）。"""
    keys = list(FACTS.keys())
    if target_fact_key and target_fact_key in keys:
        keys.remove(target_fact_key)
        keys.insert(0, target_fact_key)
    return {
        "characters": [
            {"name": PROTAGONIST, "aliases": [],
             "locked_facts": [{"fact": FACTS[k], "fact_key": k} for k in keys]},
        ],
    }


def _fixture_db(sample_dir: Path, target_fact_key: str | None) -> None:
    db = sample_dir / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    _dump(db / "人物卡.json", _character_card(target_fact_key))
    # linear 显式声明：cluster_001 默认 in_medias_res 会让 temporal scanner 整体 skip
    _dump(db / "事件簇.json", {"clusters": [
        {"cluster_id": "cluster_001", "narrative_mode": "linear", "status": "active"}]})
    # project 地点词典：spatial_continuity 走确定性 project 路径（不依赖启发式抽词）
    _dump(db / "地图.json", {"locations": list(MAP_LOCATIONS)})


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def inject(base_text: str, spec: dict) -> tuple[str, int]:
    """在段 idx 前插入注入段落。返回 (新文本, 注入文本的字符绝对偏移)。确定性纯函数。"""
    paras = base_text.split(PARA_SEP)
    idx = min(spec["paragraph_index"], len(paras))
    injected_block = PARA_SEP.join(spec["injected_paragraphs"])
    new_paras = paras[:idx] + [injected_block] + paras[idx:]
    prefix = PARA_SEP.join(paras[:idx])
    char_offset = len(prefix) + (len(PARA_SEP) if prefix else 0)
    return PARA_SEP.join(new_paras), char_offset


def _llm_augment(spec: dict) -> dict:
    """LLM 增广：真 gen-model 改写注入段（保留人名+矛盾语义·提高表面多样性）。
    env 门控 RUOYU_RUN_REAL_API=1；门控未开 → 响亮失败（绝不静默降回模板）。"""
    if os.environ.get(REAL_API_ENV) != "1":
        raise SystemExit(f"[FATAL] --mode llm 需要 {REAL_API_ENV}=1（真 API 测试纪律）；"
                         f"离线校准请用默认 --mode template")
    scripts = _REPO / "core" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import llm_transport  # noqa: E402
    from gen_model_loader import get_default_loader  # noqa: E402
    loader = get_default_loader()
    new_paras = []
    for para in spec["injected_paragraphs"]:
        fact = FACTS.get(spec["fact_key"] or "", "（无锁定事实·保持时间/空间矛盾语义）")
        result = llm_transport.generate(
            loader,
            system=("你是小说反事实注入器。把给定句子改写成风格不同但**语义等价**的一段中文小说文字："
                    "必须保留句中出现的所有人名原样，必须保留它与下列锁定事实的矛盾关系，"
                    "不得添加解释性文字，只输出改写后的段落本身。"),
            user=f"锁定事实：{fact}\n待改写句子：{para}",
            max_tokens=512, label="flawed_fiction_llm")
        text = (result.text or "").strip()
        if PROTAGONIST in para and PROTAGONIST not in text:
            raise SystemExit(f"[FATAL] LLM 改写丢失人名 {PROTAGONIST}（sample={spec['sample_id']}）"
                             f"——ground_truth 无法锚定，中止")
        new_paras.append(text)
    out = dict(spec)
    out["injected_paragraphs"] = new_paras
    out["augmented_by_llm"] = True
    return out


def build_samples(base_draft: Path, out_dir: Path, mode: str = "template") -> dict:
    """产出 baseline + 全部注入样本。返回 manifest dict（同时落盘 manifest.json）。"""
    base_text = base_draft.read_text(encoding="utf-8")
    base_sha = _sha256(base_text)
    samples_root = out_dir / "samples"
    samples_root.mkdir(parents=True, exist_ok=True)

    manifest = {"schema": "flawed_fiction_manifest_v1",
                "base_draft": str(base_draft),
                "base_draft_sha256": base_sha,
                "mode": mode,
                "protagonist": PROTAGONIST,
                "facts": dict(FACTS),
                "samples": []}

    # baseline（未注入·测误报地板）
    b_dir = samples_root / BASELINE_ID
    (b_dir / "章节" / "cluster_001_draft").mkdir(parents=True, exist_ok=True)
    (b_dir / "章节" / "cluster_001_draft" / "cluster_001_draft.txt").write_text(
        base_text, encoding="utf-8")
    _fixture_db(b_dir, None)
    gt = {"sample_id": BASELINE_ID, "flaw_type": "baseline", "character": None,
          "violated_fact": None, "injection": None, "expected_layer": None,
          "expected_deterministic_detect": False,
          "expect": {"kind": "clean"}, "base_draft_sha256": base_sha,
          "notes": "未注入基线·所有层应 0 violation（误报地板）"}
    _dump(b_dir / "ground_truth.json", gt)
    manifest["samples"].append({"sample_id": BASELINE_ID, "flaw_type": "baseline",
                                "dir": str(b_dir)})

    specs = FLAW_SPECS if mode == "template" else [_llm_augment(s) for s in FLAW_SPECS]
    for spec in specs:
        s_dir = samples_root / spec["sample_id"]
        draft_dir = s_dir / "章节" / "cluster_001_draft"
        draft_dir.mkdir(parents=True, exist_ok=True)
        corrupted, char_offset = inject(base_text, spec)
        (draft_dir / "cluster_001_draft.txt").write_text(corrupted, encoding="utf-8")
        _fixture_db(s_dir, spec["fact_key"])
        injected_text = PARA_SEP.join(spec["injected_paragraphs"])
        gt = {
            "sample_id": spec["sample_id"],
            "flaw_type": spec["flaw_type"],
            "character": PROTAGONIST,
            "violated_fact": FACTS.get(spec["fact_key"]) if spec["fact_key"] else None,
            "injection": {
                "kind": "insert_paragraphs",
                "paragraph_index": spec["paragraph_index"],
                "char_offset": char_offset,
                "injected_text": injected_text,
            },
            "expected_layer": spec["expected_layer"],
            "expected_deterministic_detect": spec["expected_deterministic_detect"],
            "expect": spec["expect"],
            "base_draft_sha256": base_sha,
            "notes": spec["notes"],
        }
        if spec.get("augmented_by_llm"):
            gt["augmented_by_llm"] = True
        _dump(s_dir / "ground_truth.json", gt)
        manifest["samples"].append({"sample_id": spec["sample_id"],
                                    "flaw_type": spec["flaw_type"], "dir": str(s_dir)})

    _dump(out_dir / "manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FlawedFictions 受控反事实注入器（S5 反向校准）")
    ap.add_argument("--draft", default=str(_DEFAULT_DRAFT), help="真 cluster 草稿路径")
    ap.add_argument("--out-dir", required=True, help="样本输出目录（勿指向仓库内）")
    ap.add_argument("--mode", choices=["template", "llm"], default="template",
                    help="template=离线确定性模板（默认）；llm=真 gen-model 增广"
                         f"（需 {REAL_API_ENV}=1）")
    args = ap.parse_args(argv)
    draft = Path(args.draft)
    if not draft.exists():
        print(f"[FATAL] 草稿不存在: {draft}", file=sys.stderr)
        return 2
    manifest = build_samples(draft, Path(args.out_dir), mode=args.mode)
    print(f"[flawed_fiction_maker] {len(manifest['samples'])} 个样本 → {args.out_dir}"
          f"（mode={manifest['mode']}·base_sha={manifest['base_draft_sha256'][:12]}）")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
