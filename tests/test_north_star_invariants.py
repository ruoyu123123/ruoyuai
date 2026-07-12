"""北极星 6 原则的结构级回归锁。

本文件只读源码和文档并调用纯函数，不接入创作运行时，不评价正文，不新增
hard_gate，也不锁定 fluid 章数。断言失败时直接暴露契约漂移。

覆盖 7 类不变量：
  ① 禁 f"cluster_{ch:03d}" 章号当 cluster 号机械拼接（cluster_lookup.py = 唯一权威反查）。
  ② chapter_splitter.py 不 import 任何 *_scanner / audit_hub（北极星④章节仅格式）。
  ③ 四方一致：audit_hub.HARD_GATE_CODES == STRUCTURE§12.2 == CLAUDE.md 清单
     == scanner_registry.json hard_gate_codes，防止多处清单漂移。
  ④ _gate_level_for 只对 HARD_GATE_CODES 判 hard_gate + scanner 升格双闸守卫。
  ⑤ 自动豁免 / 降档路径必须强制忽略 hard_gate（锁现状·守北极星⑤）。
  ⑥ P0 风格链：audit_hub --style 透传 + gen_writer 无 [:8000] 截断 / 无写死默认风。
  ⑦ 带清理死线的 DEPRECATED 标记不得过期。

🔴 改这 7 类不变量须同步改 test_north_star_invariants.py（CLAUDE.md 末尾亦有此提示）。
"""
import ast
import datetime
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
_STRUCTURE = _REPO / "core" / "claude-home" / "STRUCTURE.md"
_CLAUDE_MD = _REPO / "CLAUDE.md"

sys.path.insert(0, str(_SCRIPTS))
import audit_hub  # noqa: E402  （权威单一来源：HARD_GATE_CODES + _gate_level_for + _apply_waivers）


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ============ ① 禁 f"cluster_{ch:03d}" 机械拼接（北极星①） ============
# 精确命中字面反模式 f"cluster_{ch:03d}"（章号变量 ch 当 cluster 号）。注意：
# f"cluster_{cluster_id:03d}" / f"cluster_{next_num:03d}" / f"cluster_{i+1:03d}" 等
# 是合法的「cluster 号 → id 格式化」，不在禁列——只禁 ch（章号）当 cluster 号。
_ANTIPATTERN = re.compile(r"""f(['"])cluster_\{ch:03d\}\1""")
# 描述禁令的 doc/注释行只用于说明反模式，不算真实使用。
_BAN_DOC_MARKERS = ("禁", "取代", "旧残留", "机械拼接", "反查", "章号拼接", "消灭")
def _real_antipattern_files() -> dict:
    """返回 {文件名: [(行号, 行文本)]} —— 仅真实代码使用（排除注释 / 禁令描述行）。"""
    hits: dict = {}
    for py in sorted(_SCRIPTS.glob("*.py")):
        for i, line in enumerate(_read(py).splitlines(), 1):
            if not _ANTIPATTERN.search(line):
                continue
            code_part = line.split("#", 1)[0]          # 去掉行内 # 注释
            if "cluster_{ch:03d}" not in code_part:
                continue                                # 命中只在注释里
            if any(mk in line for mk in _BAN_DOC_MARKERS):
                continue                                # 这是描述「禁用」的 doc 行
            hits.setdefault(py.name, []).append((i, line.strip()))
    return hits


def test_no_mechanical_chapter_to_cluster_construction():
    """北极星①：禁 f"cluster_{ch:03d}" 章号当 cluster 号机械拼接。
    cluster_lookup.py = 唯一权威反查出处；任何其它文件出现该字面量 = 未授权机械拼接 → 红灯。"""
    hits = _real_antipattern_files()
    offenders = {}
    for fname, occ in hits.items():
        if fname == "cluster_lookup.py":
            continue  # 唯一权威反查 + 唯一合法字面兜底出处
        offenders[fname] = occ
    assert not offenders, (
        "发现未授权 ch→cluster 机械拼接 f\"cluster_{ch:03d}\"（违北极星①）：\n"
        + "\n".join(f"  {f}: {o}" for f, o in offenders.items())
        + "\n应改用 cluster_lookup.ch_to_cluster_id 反查。")


# ============ ② chapter_splitter 不依赖质检模块（北极星④章节仅格式） ============

def test_chapter_splitter_imports_no_scanner_or_audit_hub():
    """北极星④：splitter 是纯格式层（按字数切），不参与质检/状态/学习 →
    AST 断言它不 import 任何 *_scanner / audit_hub。"""
    tree = ast.parse(_read(_SCRIPTS / "chapter_splitter.py"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                base = n.name.split(".")[-1]
                if base.endswith("_scanner") or base == "audit_hub":
                    bad.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[-1]
            if mod.endswith("_scanner") or mod == "audit_hub":
                bad.append(node.module)
    assert not bad, f"chapter_splitter.py 不应 import 质检模块（北极星④章节仅格式）：{bad}"


# ============ ③ 四方一致：hard_gate 清单单一真理源（北极星⑤防各自另立） ============

def _structure_hard_gate_codes() -> set:
    """从 STRUCTURE.md hard_gate 主表格首列 backtick code 抽取（仅权威清单小节切片内）。"""
    text = _read(_STRUCTURE)
    start = text.find("### 12.2 hard_gate 不可豁免清单")
    end = text.find("### 12.3", start)
    assert start >= 0 and end > start, "STRUCTURE.md hard_gate 权威清单段落标记缺失"
    section = text[start:end]
    codes = set()
    for line in section.splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|", line)  # 表格行首列 backtick code
        if m:
            codes.add(m.group(1).strip())
    return codes


def _claudemd_hard_gate_codes() -> set:
    """从 CLAUDE.md「hard_gate 不可豁免清单」段（到『**权威边界**』为止）抽 backtick code。"""
    text = _read(_CLAUDE_MD)
    start = text.find("hard_gate 不可豁免清单")
    assert start >= 0, "CLAUDE.md 缺『hard_gate 不可豁免清单』段"
    end = text.find("**权威边界**", start)
    section = text[start:end] if end > start else text[start:start + 2000]
    codes = set()
    for tok in re.findall(r"`([^`]+)`", section):
        tok = tok.strip()
        # code-like：大写 ASCII 开头（含 STYLE_单段超长）·排除路径/带点的 token
        if re.match(r"^[A-Z][A-Z0-9_]", tok) and "/" not in tok and "." not in tok:
            codes.add(tok)
    return codes


def _registry_hard_gate_codes() -> set:
    """从 scanner_registry.json 顶层 hard_gate_codes[] 抽取。"""
    import json
    reg = json.loads(_read(_SCRIPTS / "scanner_registry.json"))
    codes = reg.get("hard_gate_codes")
    assert isinstance(codes, list) and codes, "scanner_registry.json 缺 hard_gate_codes[]"
    return set(codes)


def test_hard_gate_codes_four_way_consistent():
    """北极星⑤单一真理源：audit_hub.HARD_GATE_CODES == STRUCTURE§12.2 == CLAUDE.md 清单
    == scanner_registry.json hard_gate_codes。任一漂移都由断言暴露。"""
    code_set = set(audit_hub.HARD_GATE_CODES)
    struct_set = _structure_hard_gate_codes()
    claude_set = _claudemd_hard_gate_codes()
    registry_set = _registry_hard_gate_codes()
    assert code_set == struct_set, (
        f"audit_hub vs STRUCTURE§12.2 漂移 · 只在 audit_hub={code_set - struct_set} · "
        f"只在 STRUCTURE={struct_set - code_set}")
    assert code_set == claude_set, (
        f"audit_hub vs CLAUDE.md 漂移 · 只在 audit_hub={code_set - claude_set} · "
        f"只在 CLAUDE.md={claude_set - code_set}")
    assert code_set == registry_set, (
        f"audit_hub vs scanner_registry.json 漂移 · 只在 audit_hub={code_set - registry_set} · "
        f"只在 registry={registry_set - code_set}")
    # 数量变动时强制人工复核四方是否同步。
    assert len(code_set) == 18, f"HARD_GATE_CODES 数量={len(code_set)}（预期 18）·变动须四方同改"


# ============ ④ _gate_level_for 是 hard_gate 唯一裁决口 + scanner 升格双闸（北极星⑤） ============

# 已知 advisory code 抽样（绝不应被判 hard_gate）。
_KNOWN_ADVISORY = ("BANNED_WORD", "STYLE_对话占比", "STYLE_段落均长", "STYLE_长段计数",
                   "SEMANTIC_metaphor_explain", "NARRATIVE_pov", "READER_EXP_HOOK_STRENGTH",
                   "VOICE_DRIFT_CROSS_SCENE", "SEAM_DISTRIBUTION_DRIFT")


def test_gate_level_for_hard_gate_only_for_hard_gate_codes():
    """北极星⑤：error 严重度下，只有 HARD_GATE_CODES 被判 hard_gate；其余 advisory；
    且 info 严重度永不 hard_gate（低置信旁注·2026-06-02 修）。"""
    for code in audit_hub.HARD_GATE_CODES:
        assert audit_hub._gate_level_for(code, "error") == "hard_gate", code
        assert audit_hub._gate_level_for(code, "info") == "advisory", f"{code} info 不应 hard_gate"
    for code in _KNOWN_ADVISORY:
        assert audit_hub._gate_level_for(code, "error") == "advisory", code


def test_scanner_gate_upgrade_guarded_by_hard_gate_codes():
    """北极星⑤：两处 scanner 顶层 gate_level 升格双闸（_parse_issues_list_scanner +
    _parse_violations_scanner）都以 `code in HARD_GATE_CODES` 把关——防任意 scanner 在
    清单外自立 hard_gate。"""
    src = _read(_SCRIPTS / "audit_hub.py")
    guards = src.count("and code in HARD_GATE_CODES")
    assert guards >= 2, f"scanner gate 升格双闸守卫数={guards} < 2（清单外越权升 hard_gate 风险）"


# ============ ⑤ 自动豁免 / 降档必须强制忽略 hard_gate（锁现状·守北极星⑤） ============

def test_apply_waivers_never_waives_hard_gate():
    """北极星⑤：_apply_waivers 对 hard_gate 强制忽略豁免（即便传了理由）·advisory 才 waived。"""
    hard_code = sorted(audit_hub.HARD_GATE_CODES)[0]
    issues = [
        {"code": hard_code, "gate_level": "hard_gate", "waived": False, "waive_reason": ""},
        {"code": "BANNED_WORD", "gate_level": "advisory", "waived": False, "waive_reason": ""},
    ]
    waivers = [{"code": hard_code, "reason": "我想豁免它"},
               {"code": "BANNED_WORD", "reason": "本场景口语刻意为之"}]
    waived = audit_hub._apply_waivers(issues, waivers)
    assert issues[0]["waived"] is False, "hard_gate 项绝不可被豁免"
    assert issues[1]["waived"] is True, "advisory 项应可被合理豁免"
    assert all(i.get("gate_level") != "hard_gate" for i in waived)


def test_auto_calibration_softcap_caller_skips_hard_gate():
    """北极星⑤：自动降档 softcap 调用点前置 hard_gate skip（hard_gate 永不被自动降档/豁免）。
    源级锁守卫存在（函数本身依赖调用方过滤·docstring 已述）。"""
    src = _read(_SCRIPTS / "audit_hub.py")
    call = src.find("_apply_auto_calibration_softcap(issue, match)")
    assert call > 0, "未找到 _apply_auto_calibration_softcap 调用点"
    preceding = src[max(0, call - 400):call]
    assert 'gate_level") == "hard_gate"' in preceding and "continue" in preceding, (
        "softcap 调用点前缺 hard_gate skip 守卫（hard_gate 可能被自动降档·违北极星⑤）")


# ============ ⑥ P0 风格链：作者档第一权威·不截断·不写死默认风 ============

def test_audit_hub_passes_style_to_validators():
    """北极星·P0 风格链：audit_hub 把 --style 作者档透传给 validate_style（作者档=第一权威）。"""
    src = _read(_SCRIPTS / "audit_hub.py")
    assert '"--style"' in src, "audit_hub 缺 --style 透传分支"
    assert "_style_args" in src and "+ _style_args" in src, "audit_hub 未把 _style_args 拼进校验命令"


def test_gen_writer_no_prompt_truncation_no_hardcoded_default_style():
    """北极星·P0：gen_writer system prompt 不写死 [:8000] 截断（不截断喂 LLM）+
    不写死「冷峻俯瞰」类默认风（作者档优先）。"""
    src = _read(_SCRIPTS / "gen_writer.py")
    assert "[:8000]" not in src, "gen_writer 不得对 prompt/作者档做 [:8000] 硬截断"
    for i, line in enumerate(src.splitlines(), 1):
        if "冷峻俯瞰" in line:
            # 只允许出现在否定性说明里，不得作为活的默认风字符串。
            assert ("删除" in line or "删" in line or "移除" in line), (
                f"gen_writer.py:{i} 出现疑似活的『冷峻俯瞰』写死默认风：{line.strip()}")


# ============ ⑦ 清旧码：带死线的 DEPRECATED 标记未过期（北极星⑥） ============

def test_deprecated_deadlines_not_expired():
    """北极星⑥及时清旧码：带『死线 YYYY-MM-DD』的标记不得过期（过期未删=红灯）。
    到期未清理时测试转红。"""
    today = datetime.date.today()
    deadline_re = re.compile(r"死线\s*[:：]?\s*(\d{4})-(\d{2})-(\d{2})")
    expired = []
    for py in sorted(_SCRIPTS.glob("*.py")):
        for i, line in enumerate(_read(py).splitlines(), 1):
            m = deadline_re.search(line)
            if not m:
                continue
            try:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if d < today:
                expired.append(f"{py.name}:{i} 死线 {d} 已过期：{line.strip()[:80]}")
    assert not expired, "存在过期未清理的死线标记（北极星⑥）：\n" + "\n".join(expired)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
