"""novel-writer 契约 ⇄ changes_schema 对账锁。

changes_schema.json 是 `additionalProperties: false` 的封闭契约，
novel-writer.md 是唯一 producer 的提示词。两者一旦漂移，writer 自造字段
→ save_state.py --apply-cluster-changes exit 2 FATAL → gate 住整个 cluster-save-state。

本文件锁死三件事：
  ① v29 全字段 self_eval fixture 必须过 schema 校验（走 save_state 的真实校验入口）;
  ② gen_writer.py 确定性遥测（ecas_metadata）必须被 schema 接受;
  ③ schema 允许的字段 ⇄ novel-writer.md 声明的字段必须逐字段互相覆盖（无孤儿、无缺漏）。
"""

import ast
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import cluster_state_delta as csd  # noqa: E402

SCHEMA_PATH = ROOT / "core" / "claude-home" / "schemas" / "changes_schema.json"
AGENT_PATH = ROOT / ".claude" / "agents" / "novel-writer.md"
CODEX_AGENT_PATH = ROOT / ".codex" / "agents" / "novel-writer.toml"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _self_eval_props() -> dict:
    return _schema()["properties"]["self_eval"]["properties"]


def _agent_text() -> str:
    return AGENT_PATH.read_text(encoding="utf-8")


def _validate(changes: dict) -> None:
    """走 save_state.cmd_apply_cluster_changes 用的同一个校验入口。"""
    csd.validate_schema(changes, _schema())


# ---------- v29 全字段 fixture（覆盖 schema 每一个允许字段 + 每个子字段）----------

def full_v29_self_eval() -> dict:
    return {
        "self_eval": {
            "waivers": [{"code": "STYLE_长段计数", "reason": "灾难开场一口气推到底"}],
            "applied_style": {
                "opening_type": "拟声定格",
                "opening_line": "咔——",
                "opening_justification": "倒叙灾难开场",
                "ending_type": "对话悬念",
                "ending_line": "“你也听见了？”",
                "ending_justification": "留问不答",
                "applied_rules": ["R1"],
                "transitions_used": [{"from": "s0", "to": "s1"}],
                "anchors_hit": ["锚点A"],
                "core_techniques_applied": ["白描动作链"],
                "subtext_count": 3,
                "hooks_count": 2,
            },
            "uncertainty_flags": ["此处是否算剧透"],
            "moves_used": [{"character": "沈砚", "move_id": "MV_001", "instances": 2}],
            "stress_evaluation_self": {
                "estimated_stress_change": "+2",
                "violations_made": ["V1"],
                "alignments_made": ["A1"],
                "coping_behaviors_used": ["酗酒"],
            },
            "storyteller_alignment": {
                "target_outcome_followed": "setback",
                "actual_outcome": "setback",
                "phase_alignment_evidence": "主角失手",
            },
            "offscreen_actions_executed": [
                {"character": "李昭", "action_index": 0, "completed_fully": True}
            ],
            "writer_mode": "claude_draft_gemini_polish_v29",
            "cluster_id": "cluster_001",
            "narrative_mode": "in_medias_res",
            "narrative_pov_mode": "third_limited",
            "scene_count": 5,
            "claude_draft_cjk": 10008,
            "foreshadowing_planted_surface": ["F_001"],
            "dialogue_telemetry": {
                "dialogue_cjk": 2100,
                "dialogue_ratio": 0.21,
                "external_dialogue_ratio": 0.15,
                "quoted_inner_ratio": 0.06,
                "claude_draft_dialogue_ratio": 0.19,
                "polished_dialogue_cjk": 2050,
                "polished_dialogue_ratio": 0.205,
                "quote_guard": {
                    "scenes_triggered": 1,
                    "scenes_retried": 1,
                    "scenes_kept_claude": 0,
                    "floor_ratio": 0.85,
                    "min_abs_drop": 0.02,
                },
                "note": "润色后对话占比核修",
            },
            "ecas_metadata": {
                "cluster_id": "cluster_001",
                "writer_mode": "claude_draft_gemini_polish_v29",
                "chapter_count_decided_by_splitter": True,
                "ch_start": 1,
                "ch_range": "1-TBD_by_splitter",
                "generated_by": "novel-writer(claude 亲笔草稿) + gen_writer.py(gemini 分段润色)",
                "generated_by_profile": "pie-xian",
                "generated_by_model": "gemini-3.1-pro-preview",
                "generated_at": "2026-07-14T23:30:16",
                "cjk_actual": 12961,
                "length_telemetry": {"score": 1.0, "band": [10000, 25000]},
                "polish": [{"scene": "scene_00", "ratio": 1.12}],
                "post_polish_deterministic_fixes": {"excised": 0, "reflowed": 3},
            },
        }
    }


def test_v29_full_self_eval_passes_schema():
    """回归锁：v29 全字段 self_eval 必须过校验（producer 真实输出不得再 FATAL）。"""
    _validate(full_v29_self_eval())


def test_minimal_self_eval_passes():
    """只有 waivers 也合法（其余字段按适用性填）。"""
    _validate({"self_eval": {"waivers": []}})


def test_waivers_is_required():
    with pytest.raises(ValueError):
        _validate({"self_eval": {"scene_count": 3}})


@pytest.mark.parametrize("field,value", [
    ("self_eval", {"waivers": [], "hallucinated_field": 1}),
    ("applied_style", {"waivers": [], "applied_style": {"made_up": "x"}}),
    ("dialogue_telemetry", {"waivers": [], "dialogue_telemetry": {"made_up": 1}}),
])
def test_unknown_field_is_fatal(field, value):
    """封闭清单：writer 自造字段必须硬失败（这就是 gate 住 cluster 的那一刀）。"""
    with pytest.raises(ValueError):
        _validate({"self_eval": value})


def test_factual_top_level_rejected():
    """v29 已收口单一 self_eval 契约——旧 factual 顶层字段不得复活。"""
    with pytest.raises(ValueError):
        _validate({"self_eval": {"waivers": []}, "factual": {"characters": []}})


# ---------- gen_writer 确定性遥测必须被 schema 接受 ----------

def test_gen_writer_ecas_metadata_keys_accepted():
    """gen_writer.save_output() 写的每个 ecas_metadata key 都要能过校验。"""
    produced = full_v29_self_eval()["self_eval"]["ecas_metadata"]
    for key in ("cluster_id", "ch_start", "ch_range", "chapter_count_decided_by_splitter",
                "generated_by", "generated_by_profile", "generated_by_model",
                "generated_at", "cjk_actual", "writer_mode", "length_telemetry"):
        assert key in produced, f"gen_writer 产的 {key} 未进 fixture"
    _validate({"self_eval": {"waivers": [], "ecas_metadata": produced}})


# ---------- agent 契约 ⇄ schema 逐字段对账（防漂移）----------

def test_agent_md_documents_every_schema_field():
    """schema 允许的每个 self_eval 字段都必须在 novel-writer.md 里写明。

    漏写 → writer 不知道能填 → 下游 aggregator 断层。
    """
    text = _agent_text()
    missing = [f for f in _self_eval_props() if f"`{f}`" not in text]
    assert not missing, f"novel-writer.md 未声明 schema 字段: {missing}"


def test_agent_md_json_example_is_schema_valid():
    """novel-writer.md 里给 writer 抄的 JSON 示例本身必须过 schema 校验。"""
    blocks = re.findall(r"```json\n(.*?)```", _agent_text(), re.S)
    examples = []
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "self_eval" in obj:
            examples.append(obj)
    assert examples, "novel-writer.md 缺少 self_eval JSON 示例"
    for example in examples:
        _validate(example)


def test_agent_md_declares_no_field_outside_schema():
    """反向对账：md 的 JSON 示例不得出现 schema 之外的 self_eval 字段。"""
    allowed = set(_self_eval_props())
    blocks = re.findall(r"```json\n(.*?)```", _agent_text(), re.S)
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("self_eval"), dict):
            extra = set(obj["self_eval"]) - allowed
            assert not extra, f"novel-writer.md 示例含 schema 外字段: {extra}"


def test_dialogue_telemetry_quote_guard_is_closed():
    """quote_guard 子对象也是封闭清单：gen_writer 之外不得夹带自由字段。"""
    with pytest.raises(ValueError):
        _validate({"self_eval": {"waivers": [], "dialogue_telemetry": {
            "quote_guard": {"made_up": 1}}}})


def test_agent_md_documents_dialogue_telemetry_fields():
    """dialogue_telemetry 封闭清单的每个字段都必须在 novel-writer.md 里写明（嵌套级对账）。"""
    props = _self_eval_props()["dialogue_telemetry"]["properties"]
    text = _agent_text()
    missing = [f for f in props if f"`{f}`" not in text]
    assert not missing, f"novel-writer.md 未声明 dialogue_telemetry 字段: {missing}"


def _audit_hub_registered_codes() -> set[str]:
    """AST 精确提取 audit_hub.py issue code 注册表的键集合。

    子串包含判断会被「前缀幽灵码」骗过（幽灵 `STYLE_长段` 是真码 `STYLE_长段计数`
    的前缀，`c in src` 误过）——必须做精确成员判断。注册表结构（与 audit_hub 源码对齐）：
      · HARD_GATE_CODES / DETERMINISTIC_FIX_CODES：set 字面量元素
      · AGENT_ROUTING：dict 键
      · STYLE_DIM：validate_style 项名，emit 时组成 f"STYLE_{name}"（audit_hub 同款）
      · SEMANTIC_DIM：semantic_slop 检测器名，codes 形如 SEMANTIC_<detector>
      · FLAT_FSCANNER_CODE：dict 值（READER_EXP_*）
    结构漂移（改名/改字面量类型）→ 这里响亮报错，同步更新本锁。"""
    src = (ROOT / "core" / "scripts" / "audit_hub.py").read_text(encoding="utf-8")
    tops: dict[str, ast.expr] = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            tops[node.targets[0].id] = node.value

    def _str_consts(nodes) -> set[str]:
        return {n.value for n in nodes
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    def _set_elems(name: str) -> set[str]:
        node = tops.get(name)
        assert isinstance(node, ast.Set), \
            f"audit_hub.{name} 不是 set 字面量（注册表结构漂移·同步更新本锁）"
        return _str_consts(node.elts)

    def _dict_keys(name: str) -> set[str]:
        node = tops.get(name)
        assert isinstance(node, ast.Dict), \
            f"audit_hub.{name} 不是 dict 字面量（注册表结构漂移·同步更新本锁）"
        return _str_consts(node.keys)

    def _dict_values(name: str) -> set[str]:
        node = tops.get(name)
        assert isinstance(node, ast.Dict), \
            f"audit_hub.{name} 不是 dict 字面量（注册表结构漂移·同步更新本锁）"
        return _str_consts(node.values)

    codes = _set_elems("HARD_GATE_CODES") | _set_elems("DETERMINISTIC_FIX_CODES")
    codes |= _dict_keys("AGENT_ROUTING")
    codes |= {f"STYLE_{k}" for k in _dict_keys("STYLE_DIM")}
    codes |= {f"SEMANTIC_{k}" for k in _dict_keys("SEMANTIC_DIM")}
    codes |= _dict_values("FLAT_FSCANNER_CODE")
    assert codes, "audit_hub code 注册表提取为空（结构漂移·同步更新本锁）"
    return codes


def _example_waiver_codes(text: str) -> set[str]:
    """收集文档里出现的示例 waiver code：JSON 示例 waivers[].code + 行文 STYLE_* 引用。"""
    codes = set()
    # ① JSON 示例里的 waivers[].code（"…"/"..." 之类 schema 占位符不算 code）
    for block in re.findall(r"```json\n(.*?)```", text, re.S):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        se = obj.get("self_eval") if isinstance(obj, dict) else None
        if isinstance(se, dict):
            for w in se.get("waivers") or []:
                if isinstance(w, dict) and w.get("code"):
                    c = str(w["code"])
                    if re.fullmatch(r"\w+", c):
                        codes.add(c)
    # ② 行文里引用的 STYLE_* 前缀 code（issue code 唯一无歧义命名空间）
    codes |= set(re.findall(r"STYLE_\w+", text))
    return codes


@pytest.mark.parametrize("doc_path", [AGENT_PATH, CODEX_AGENT_PATH],
                         ids=["claude-novel-writer-md", "codex-novel-writer-toml"])
def test_agent_md_waiver_example_codes_exist_in_audit_hub(doc_path):
    """🔴 waiver code 禁自造回归锁：writer 提示词（.claude md + .codex toml）出现的
    全部示例 waiver code 必须【精确等于】audit_hub.py code 注册表里真实存在的 code
    （防文档示例本身漂移成幽灵 code——长恨 cluster_001 实证：writer 抄了 md 风格的
    自造 code `DIALOGUE_RATIO_BELOW_AUTHOR_BASELINE`，3 条 waiver 全 orphan 失效；
    子串包含判断会放过 `STYLE_长段` 这类真码前缀幽灵）。"""
    assert doc_path.exists(), f"writer 提示词缺失: {doc_path}"
    registered = _audit_hub_registered_codes()
    codes = _example_waiver_codes(doc_path.read_text(encoding="utf-8"))
    assert codes, f"{doc_path.name} 应至少含一个示例 waiver code"
    ghosts = sorted(c for c in codes if c not in registered)
    assert not ghosts, f"{doc_path.name} 示例 waiver code 不在 audit_hub 注册表: {ghosts}"


def test_schema_has_no_orphan_field():
    """schema 不得留无 producer 无 consumer 的孤儿字段（清旧防误导）。

    每个字段至少要么被 core/scripts 消费，要么在 novel-writer.md 里被要求产出。
    """
    consumers = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (ROOT / "core" / "scripts").glob("*.py")
    )
    text = _agent_text()
    orphans = [f for f in _self_eval_props()
               if f not in consumers and f"`{f}`" not in text]
    assert not orphans, f"schema 孤儿字段（无消费者无产出方）: {orphans}"
