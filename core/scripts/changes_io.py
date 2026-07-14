#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""changes_io.py — cluster changes.json 确定性字数/对话遥测的唯一回写入口。

cluster 草稿的字数遥测（`self_eval.ecas_metadata` 的 `cjk_actual` / `word_count_cjk` /
`length_telemetry`）与对话遥测（`self_eval.dialogue_telemetry` 的 `polished_dialogue_cjk` /
`polished_dialogue_ratio`·引号口径 = style_analyzer.calc_dialogue_ratio 与作者基线同一把尺）
必须永远等于磁盘上 `cluster_<key>_draft.txt` 的真值。写草稿的两个脚本
——`gen_writer.py`（首次落稿：Claude 场景稿 → gemini 分段润色）和 `gen_fixer.py`
（改稿：comprehensive / polish / validator-repair / voice-fix 原地覆写草稿）——都经本模块
回写，不各写各的。

遥测脱节的下游后果是硬的：`writer_truth_check.py` 把 `ecas_metadata.cjk_actual != 正文实际
CJK` 计为 writer 说谎（lie），`cluster-save-state` step 3 见 `lie_count != 0` 直接阻断流水线。

字数口径走 `text_metrics.count_cjk`（全仓 CJK 唯一实现）；落盘走 `atomic_json`（原子写唯一
实现）；schema 收口走 `chapter_io.normalize_changes`（`{"self_eval": {...}}` 唯一合同）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atomic_json import atomic_write_json, load_json_strict  # noqa: E402
from chapter_io import normalize_changes  # noqa: E402
from text_metrics import count_cjk  # noqa: E402


class ChangesIOError(RuntimeError):
    """cluster 草稿 / changes.json 契约破损：路径不是 cluster 草稿、草稿缺失、changes 缺失或损坏。"""


_DRAFT_PREFIX = "cluster_"
_DRAFT_SUFFIX = "_draft.txt"


# ============ 路径 ============
def changes_path_for_draft(draft_path) -> Path:
    """cluster 草稿路径 → 同目录 changes.json 路径。

    `章节/cluster_006_draft/cluster_006_draft.txt`
      → `章节/cluster_006_draft/cluster_006_changes.json`

    非 cluster 草稿路径抛 ChangesIOError —— gen_writer / gen_fixer 只在 cluster 草稿层工作
    （北极星④：物理章是格式层输出，不是写作/质检载体），写到别处就是流程违规，响亮失败。
    """
    p = Path(draft_path)
    name = p.name
    if not (name.startswith(_DRAFT_PREFIX) and name.endswith(_DRAFT_SUFFIX)):
        raise ChangesIOError(
            f"非 cluster 草稿路径（期望 cluster_<key>_draft.txt）: {p}")
    key = name[len(_DRAFT_PREFIX):-len(_DRAFT_SUFFIX)]
    if not key:
        raise ChangesIOError(f"cluster 草稿文件名缺 key: {p}")
    return p.parent / f"{_DRAFT_PREFIX}{key}_changes.json"


# ============ 长度遥测（cjk 的派生量·与 cjk 同源同刻回写） ============
def length_telemetry_band() -> tuple:
    """遥测带宽：与 cluster_length_band_scanner._band() 同源同口径（默认 [12000, 25000] ·
    env CLUSTER_LENGTH_BAND_OVERRIDE="min,max" 覆盖 · 单一真理源不各算各的）。"""
    import cluster_length_band_scanner as clbs
    lo, hi, _note = clbs._band()
    return lo, hi


def length_telemetry_score(cjk: int, band: tuple = None) -> float:
    """cluster 长度连续遥测分 0-100（LongWriter 非对称公式：偏短罚陡 /2 · 超长罚缓 /3）。

    带内 = 100；y < min → 100 * max(0, 1 - (min/y - 1)/2)；
    y > max → 100 * max(0, 1 - (y/max - 1)/3)；y <= 0 → 0。
    长度带 scanner 只能二值拒绝，本分把带外偏差量化成连续 reward 特征。
    仅遥测——不进择稿、不 hard_gate、不回流 writer prompt。
    """
    lo, hi = band if band else length_telemetry_band()
    y = float(cjk)
    if y <= 0:
        return 0.0
    if y < lo:
        return round(100.0 * max(0.0, 1.0 - (lo / y - 1.0) / 2.0), 2)
    if y > hi:
        return round(100.0 * max(0.0, 1.0 - (y / hi - 1.0) / 3.0), 2)
    return 100.0


# ============ 对话遥测（引号占比·与字数同刻回写） ============
def _dialogue_telemetry_from_body(body: str) -> tuple:
    """磁盘终稿的引号对话遥测 (dialogue_cjk, dialogue_ratio)。

    口径 = style_analyzer.DIALOGUE_QUOTED / calc_dialogue_ratio（与作者基线 dialogue_ratio /
    validate_style 同一把尺·禁自写第二实现）。lazy import 保持模块加载轻量。
    """
    import style_analyzer as sa
    dlg_cjk = sum(sa.count_chinese(m) for m in sa.DIALOGUE_QUOTED.findall(body))
    return dlg_cjk, sa.calc_dialogue_ratio(body)


# ============ 回写 ============
def sync_cjk_actual(draft_path, *, changes: dict | None = None) -> dict:
    """把 cluster changes.json 的字数/对话遥测对齐磁盘草稿真值（唯一回写入口）。

    · `changes=None`（gen_fixer 改稿后）→ 读同目录 changes.json 当基底，只覆盖字数遥测字段，
      其余 self_eval（applied_style / waivers / uncertainty_flags / polish …）原样保留。
    · `changes=dict`（gen_writer 首次落稿）→ 用调用方组装好的 changes 当基底落盘。

    CJK 一律从**磁盘上的草稿**重算（不信调用方内存里的 body），保证 changes 声明的字数与
    下游 writer_truth_check / splitter 读到的是同一份正文。

    返回 {"cjk": int, "changes_path": Path, "changes": dict}。契约破损抛 ChangesIOError。
    """
    p = Path(draft_path)
    changes_path = changes_path_for_draft(p)
    if not p.is_file():
        raise ChangesIOError(f"cluster 草稿不存在，无法回写字数遥测: {p}")

    body_text = p.read_text(encoding="utf-8")
    cjk = count_cjk(body_text)

    if changes is None:
        if not changes_path.is_file():
            # cluster 草稿必须有同名 changes.json（CHANGES_MISSING 是 hard_gate 文件契约）。
            raise ChangesIOError(f"cluster changes.json 缺失，无法回写字数遥测: {changes_path}")
        changes = load_json_strict(changes_path, exc=ChangesIOError)

    data = normalize_changes(changes)
    self_eval = data["self_eval"]
    meta = self_eval.get("ecas_metadata")
    if not isinstance(meta, dict):
        meta = {}
        self_eval["ecas_metadata"] = meta

    band = length_telemetry_band()
    meta["cjk_actual"] = cjk
    # writer_truth_check 按 cjk_actual → word_count_cjk → final_cjk 顺序取第一个非空值当
    # 「writer 声明的字数」；别名同刻回写成同一真值，杜绝任一别名残留过期值形成第二口径。
    meta["word_count_cjk"] = cjk
    if "final_cjk" in meta:
        meta["final_cjk"] = cjk
    meta["length_telemetry"] = {
        "score": length_telemetry_score(cjk, band),
        "band": list(band),
        "formula": "longwriter_asymmetric(under/2, over/3)",
    }

    # 对话遥测（polished_*）与字数同刻从磁盘终稿回写——gen_writer 首次落稿与 gen_fixer
    # 改稿共用同一入口，杜绝润色/改稿后 polished_dialogue_* 残留过期值形成第二口径。
    dlg_cjk, dlg_ratio = _dialogue_telemetry_from_body(body_text)
    dt = self_eval.get("dialogue_telemetry")
    if not isinstance(dt, dict):
        dt = {}
        self_eval["dialogue_telemetry"] = dt
    dt["polished_dialogue_cjk"] = dlg_cjk
    dt["polished_dialogue_ratio"] = round(dlg_ratio, 4)

    atomic_write_json(changes_path, data)
    return {"cjk": cjk, "changes_path": changes_path, "changes": data}
