# -*- coding: utf-8 -*-
"""test_wpb_bpr_eval.py — S6 WritingPreferenceBench 外部校准脚本回归测试。

确定性·不依赖真数据集（HF 下载物不入库）——全部用合成偏好对。

覆盖：
  1. 粗桶映射确定性（显式表·未列 tag 落 functional_practical）
  2. accuracy 行：打平计 0.5 / 空集 None
  3. load_pairs：缺 response 的条目剔除
  4. kfold 评估确定性（同 seed 两次同结果）+ 可分离合成数据上 accuracy > 0.5
  5. 特征激活审计：散文映射只激活 scope_length / history_keyword_overlap（适配缺口实证锁）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CALIB_DIR = _ROOT / "core" / "ml" / "calibration"
if str(_CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(_CALIB_DIR))

import wpb_bpr_eval as mod  # noqa: E402


def test_coarse_bucket_mapping():
    assert mod.coarse_bucket("武侠小说") == "creative_fiction"
    assert mod.coarse_bucket("抽象文学-亚文化") == "abstract_subculture"
    assert mod.coarse_bucket("诗歌") == "poetry_prose_letters"
    assert mod.coarse_bucket("会议纪要") == "functional_practical"
    assert mod.coarse_bucket("完全未知类型") == "functional_practical"


def test_accuracy_row_ties_and_empty():
    assert mod._accuracy_row([]) == {"n": 0, "accuracy": None, "ties": 0}
    row = mod._accuracy_row([(1.0, 0.0), (0.5, 0.5)])  # 一胜一平 → (1+0.5)/2
    assert row["accuracy"] == 0.75 and row["ties"] == 1


def test_load_pairs_drops_incomplete(tmp_path):
    data = [
        {"tag": "武侠小说", "chosen": {"response": "甲"}, "rejected": {"response": "乙"}},
        {"tag": "诗歌", "chosen": {"response": ""}, "rejected": {"response": "乙"}},
        {"tag": "散文", "chosen": {"response": "甲"}, "rejected": {}},
    ]
    p = tmp_path / "wpb.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pairs = mod.load_pairs(p)
    assert len(pairs) == 1 and pairs[0]["bucket"] == "creative_fiction"


def _synthetic_pairs(n: int = 40) -> list:
    """可分离合成对：chosen 恒比 rejected 长很多 → scope_length 特征应能学出来。"""
    pairs = []
    for i in range(n):
        pairs.append({
            "tag": "武侠小说" if i % 2 == 0 else "会议纪要",
            "bucket": mod.coarse_bucket("武侠小说" if i % 2 == 0 else "会议纪要"),
            "chosen": ("剑光如雨落，江湖夜雨十年灯。" * 20) + f"第{i}篇",
            "rejected": f"短文{i}。",
        })
    return pairs


def test_kfold_deterministic_and_learns_separable_signal():
    pairs = _synthetic_pairs()
    r1 = mod.kfold_eval(pairs, folds=4, seed=7)
    r2 = mod.kfold_eval(pairs, folds=4, seed=7)
    assert r1["overall"] == r2["overall"]
    assert r1["per_bucket"] == r2["per_bucket"]
    # 完全可分（chosen 恒长 + 恒定风格词重叠）→ 应显著高于随机
    assert r1["overall"]["accuracy"] > 0.9


def test_active_feature_audit_locks_adaptation_gap():
    pairs = _synthetic_pairs()
    r = mod.kfold_eval(pairs, folds=4, seed=7)
    assert set(r["active_feature_audit"]) <= {"scope_length", "history_keyword_overlap"}, \
        "散文映射激活了结构化特征——to_candidate 契约被改动？适配缺口结论需要重审"
