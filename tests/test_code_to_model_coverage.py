# 🔴 2026-07-02 CODE_TO_MODEL 桶表全仓对账 · 回归锁
"""test_code_to_model_coverage.py — 防「新 scanner 忘记注册数据飞轮桶表」复发。

背景：core/scripts/ 里 209 个 scanner/aggregate 各自 emit issue code，
data_collector.py 的 _collect_weak_labels / _collect_strong_labels 靠
code_to_model_table.CODE_TO_MODEL.get(code) 把 issue 路由进训练池——查不到就静默丢弃。
近几批新 scanner（R8-R25 批次）合计 45+ code 曾经忘记注册；2026-07-02 全仓 AST 静态
扫描一次核出 468 个从未注册的 code，逐一归桶/豁免补齐。

本测试独立用 AST 重新扫一遍 core/scripts/*.py 的静态 code（不信任 code_to_model_table.py
自己的记录，做外部交叉核对），断言每个静态 code 都能被下面三选一覆盖：
  1. 精确命中 CODE_TO_MODEL
  2. 前缀命中 CODE_PREFIX_TO_MODEL（audit_hub 等运行时 f-string 拼码的场景）
  3. 显式登记在 EXCLUDED_FROM_FLYWHEEL（非正文创作判断信号，刻意不收）
以后新 scanner 忘记三选一都做 → 这个测试直接测红，倒逼当场补注册。

提取逻辑刻意保守（宁可漏判为"动态"跳过断言，不能把非 code 的字符串误判成 code）：
  规则 A：字典字面量 {"code": ...} / {"issue": ...} 的值
  规则 B：NAME = "VALUE" 标量赋值，VALUE 形如 SCREAMING_SNAKE 且 NAME 含 CODE/ISSUE 或 NAME==VALUE
  规则 C：NAME = ("A", "B", ...) / {"k": "A", ...} 容器赋值，NAME 含 CODE/ISSUE，展开每个元素
  三角覆盖：值本身可以是 Constant / 可解析的 Name（回指规则 B/C 的常量）/ IfExp 两分支递归解析
f-string / 二元拼接 / 未登记的局部变量 → 归入 dynamic_hints，只打印不断言（这类多是运行时
拼码，如 audit_hub 的 STYLE_{name} 系列，本就不是本测试的断言范围，见 code_to_model_table.py
CODE_PREFIX_TO_MODEL 头部注释里的说明）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "core" / "scripts"
_FLYWHEEL_DIR = _REPO_ROOT / "core" / "ml" / "flywheel"

sys.path.insert(0, str(_FLYWHEEL_DIR))
from code_to_model_table import (  # noqa: E402
    CODE_TO_MODEL,
    CODE_PREFIX_TO_MODEL,
    EXCLUDED_FROM_FLYWHEEL,
    resolve_model_for_code,
)

_CODE_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_DICT_CODE_KEYS = ("code", "issue", "issue_code")


def _collect_code_shaped_constants(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """扫模块内所有 NAME = "VALUE" / NAME = (容器) 赋值。

    返回 (name_to_value, container_derived_codes)：
      name_to_value —— 标量常量 identifier -> value，用于回指解析 ast.Name 引用
      container_derived_codes —— 元组/列表/字典容器里展开出的 code 值（直接就是最终结果，
        因为这类值多半靠下标/键访问消费，没法用单一 identifier 回指）
    """
    name_to_value: dict[str, str] = {}
    container_codes: dict[str, str] = {}

    for node in ast.walk(tree):
        targets, value = None, None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if targets is None:
            continue
        for t in targets:
            if not isinstance(t, ast.Name):
                continue
            name = t.id
            name_is_code_shaped = "CODE" in name.upper() or "ISSUE" in name.upper()
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                val = value.value
                if _CODE_SHAPE.match(val) and (name_is_code_shaped or name == val):
                    name_to_value[name] = val
            elif isinstance(value, (ast.Tuple, ast.List)) and name_is_code_shaped:
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and _CODE_SHAPE.match(elt.value):
                        container_codes[elt.value] = elt.value
            elif isinstance(value, ast.Dict) and name_is_code_shaped:
                for v in value.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str) and _CODE_SHAPE.match(v.value):
                        container_codes[v.value] = v.value
    return name_to_value, container_codes


def extract_codes_from_file(path: Path) -> tuple[set[str], list[str]]:
    """返回 (static_codes, dynamic_hints)。语法错误/编码错误的文件静默跳过（不是本测试关注点）。"""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return set(), []

    consts, container_codes = _collect_code_shaped_constants(tree)
    static_codes: set[str] = set(container_codes.values())
    static_codes.update(consts.values())
    dynamic: list[str] = []

    def resolve_value(v, line: int) -> None:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            if _CODE_SHAPE.match(v.value):
                static_codes.add(v.value)
            # else: 非 code 形态的自由文本（如 "issue" 键装描述），忽略
        elif isinstance(v, ast.Constant) and v.value is None:
            pass  # 显式"无 code"分支
        elif isinstance(v, ast.Name):
            if v.id in consts:
                static_codes.add(consts[v.id])
            else:
                dynamic.append(f"{path.name}:{line}:unresolved_name[{v.id}]")
        elif isinstance(v, ast.IfExp):
            resolve_value(v.body, getattr(v.body, "lineno", line))
            resolve_value(v.orelse, getattr(v.orelse, "lineno", line))
        elif isinstance(v, ast.JoinedStr):
            dynamic.append(f"{path.name}:{line}:fstring")
        else:
            dynamic.append(f"{path.name}:{line}:other[{type(v).__name__}]")

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value in _DICT_CODE_KEYS:
                    resolve_value(v, getattr(v, "lineno", getattr(node, "lineno", 0)))

    return static_codes, dynamic


def extract_all_codes() -> tuple[dict[str, list[str]], list[str]]:
    """扫 core/scripts/*.py 全部文件。返回 (code -> [文件名,...], 全部 dynamic_hints)。"""
    code_sources: dict[str, list[str]] = {}
    all_dynamic: list[str] = []
    for path in sorted(_SCRIPTS_DIR.glob("*.py")):
        codes, dynamic = extract_codes_from_file(path)
        for code in codes:
            code_sources.setdefault(code, []).append(path.name)
        all_dynamic.extend(dynamic)
    return code_sources, all_dynamic


def _is_covered(code: str) -> bool:
    if resolve_model_for_code(code) is not None:
        return True
    return code in EXCLUDED_FROM_FLYWHEEL


@pytest.fixture(scope="module")
def scanned():
    code_sources, dynamic = extract_all_codes()
    return code_sources, dynamic


def test_scripts_dir_exists():
    assert _SCRIPTS_DIR.is_dir(), f"core/scripts 目录不存在: {_SCRIPTS_DIR}"


def test_extraction_finds_known_codes(scanned):
    """提取逻辑自检：确认扫描器本身没坏——已知几个不同写法惯例的 code 都应该被找到。"""
    code_sources, _ = scanned
    # module-level 标量常量 + 位置传参给 _make_issue 风格（surprisal_scanner.py）
    assert "SURPRISAL_TOO_FLAT" in code_sources
    # 字典字面量直写（cross_cluster_continuity_aggregate.py 等）
    assert "TIME_JUMP_UNEXPLAINED" in code_sources
    # ISSUE_CODES = (...) 容器展开（allusion_ledger_scanner.py）
    assert "CHENGYU_MISUSE_SUSPECTED" in code_sources
    # ISSUE_CODES = {...} dict 容器展开（character_consistency_scanner.py）
    assert "CHARACTER_VANISH" in code_sources


def test_every_static_code_is_covered(scanned):
    """核心回归锁：core/scripts 里每个静态 issue code 必须能被
    CODE_TO_MODEL ∪ CODE_PREFIX_TO_MODEL ∪ EXCLUDED_FROM_FLYWHEEL 三选一覆盖。

    失败时说明有新 scanner（或改名的旧 scanner）忘记注册训练桶——按失败信息里列出的
    code 逐个决定该进哪个桶（13 桶之一 / judge_reliability 等既有池名）还是该显式
    EXCLUDED（附理由），然后去 code_to_model_table.py 补上。
    """
    code_sources, _ = scanned
    uncovered = {code: files for code, files in code_sources.items() if not _is_covered(code)}
    if uncovered:
        lines = [f"  {code}  (来自: {', '.join(files)})" for code, files in sorted(uncovered.items())]
        pytest.fail(
            f"{len(uncovered)} 个静态 issue code 未注册进 CODE_TO_MODEL / "
            f"CODE_PREFIX_TO_MODEL / EXCLUDED_FROM_FLYWHEEL，训练信号会被 "
            f"data_collector._collect_weak_labels 静默丢弃：\n" + "\n".join(lines)
        )


def test_no_code_both_registered_and_excluded():
    """一致性检查：不该有 code 同时在 CODE_TO_MODEL 又在 EXCLUDED_FROM_FLYWHEEL——
    说明桶表自己内部口径打架。"""
    overlap = set(CODE_TO_MODEL) & set(EXCLUDED_FROM_FLYWHEEL)
    assert not overlap, f"code 同时注册又豁免，口径冲突: {sorted(overlap)}"


def test_prefix_table_entries_are_uppercase_and_registered_in_a_real_bucket():
    """CODE_PREFIX_TO_MODEL 的前缀本身要形似 SCREAMING_SNAKE_（大写+下划线结尾），
    且映射到的桶名不能是拼错的桶（必须是 CODE_TO_MODEL 里实际用过的某个桶值）。"""
    valid_models = set(CODE_TO_MODEL.values())
    for prefix, model in CODE_PREFIX_TO_MODEL.items():
        assert prefix == prefix.upper(), f"前缀应全大写: {prefix!r}"
        assert prefix.endswith("_"), f"前缀应以下划线结尾避免误匹配同族其他 code: {prefix!r}"
        assert model in valid_models, f"前缀 {prefix!r} 指向的桶 {model!r} 不是任何已注册 code 用过的桶名"


def test_excluded_codes_have_non_trivial_reasons():
    """EXCLUDED_FROM_FLYWHEEL 的每条豁免都要有实质理由字符串，不能是空字符串占位。"""
    for code, reason in EXCLUDED_FROM_FLYWHEEL.items():
        assert isinstance(reason, str) and len(reason) >= 8, (
            f"{code} 的豁免理由太短/为空，看起来像占位而非真实理由: {reason!r}"
        )


def test_resolve_model_for_code_is_case_insensitive():
    """根治性验证：audit_hub._parse_scanner_json 用 f"{scanner.upper()}_{check}" 拼 code 时
    check 部分保留小写（如 "SEMANTIC_metaphor_explain"），resolve_model_for_code 必须大小写
    不敏感才能命中已注册的 "SEMANTIC_METAPHOR_EXPLAIN"，否则这批训练信号会静默丢失。"""
    assert resolve_model_for_code("SEMANTIC_metaphor_explain") == "ai_tone"
    assert resolve_model_for_code("semantic_metaphor_explain") == "ai_tone"
    assert resolve_model_for_code("SEMANTIC_METAPHOR_EXPLAIN") == "ai_tone"


def test_resolve_model_for_code_prefix_fallback():
    assert resolve_model_for_code("CHARACTER_ARC_DRIFT_XIAOMING") == "character_trajectory"
    assert resolve_model_for_code("BIBER_MDA_DRIFT_D1") == "style_fidelity"


def test_resolve_model_for_code_none_cases():
    assert resolve_model_for_code(None) is None
    assert resolve_model_for_code("") is None
    assert resolve_model_for_code("TOTALLY_UNREGISTERED_CODE_XYZ") is None
    # 显式豁免的 code 不应该被 resolve 出桶名（它不在 CODE_TO_MODEL 里，只在 EXCLUDED 表）
    assert resolve_model_for_code("LOCKED_FACT_CONFLICT") is None


if __name__ == "__main__":
    # 独立跑一遍打印覆盖率概况 + dynamic hints 清单，方便人工核对新增 scanner。
    sources, dynamic_hints = extract_all_codes()
    uncovered_map = {c: f for c, f in sources.items() if not _is_covered(c)}
    print(f"core/scripts 静态 code 总数: {len(sources)}")
    print(f"未覆盖: {len(uncovered_map)}")
    for c, f in sorted(uncovered_map.items()):
        print(f"  {c}  <- {', '.join(f)}")
    print(f"\ndynamic（无法静态解析，仅供参考不做断言）: {len(dynamic_hints)}")
    for h in dynamic_hints:
        print(f"  {h}")
