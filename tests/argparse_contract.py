# -*- coding: utf-8 -*-
"""argparse 契约干跑校验器（测试基础设施 · 无副作用）。

用途：给定一个脚本文件和一串 argv，回答「这个脚本的 argparse 能不能接受这串 argv」，
**不 import 也不执行**被测脚本（scanner 里有重模型/子进程/daemon 依赖，import 即污染）。

做法：AST 抽取脚本源码里所有 `*.add_argument(...)` 调用的字面量声明（选项名 + action /
nargs / type / required / choices / const / default / dest），重建一个等价的
argparse.ArgumentParser，再拿目标 argv 去 parse。argparse 自身的错误（未知选项 /
flag 被传了值 / 带值选项缺值 / 位置参数多了少了 / type 不匹配 / choices 不匹配）
就是「调用方 argv 与脚本契约不兼容」。

病史：audit_hub cluster 模式给 spatial_continuity_scanner 传 `--cluster cluster_001`（带值），
而该 scanner 的 `--cluster` 是 `action='store_true'`（开关）→ argparse exit 2 →
scanner 从未产出过结果。回归锁见 tests/test_audit_hub_argv_contract.py。
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

# 只重建这些字面量 kwarg（其余如 help/metavar 不影响可接受性）
_KWARG_WHITELIST = {
    "action", "nargs", "const", "default", "choices", "required", "dest",
}
_TYPE_MAP = {"int": int, "float": float, "str": str}


class ContractError(Exception):
    """argparse 拒绝了这串 argv。"""


class _RaisingParser(argparse.ArgumentParser):
    """把 argparse 的 exit-2 行为换成抛异常（测试进程不能被 SystemExit 打断）。"""

    def error(self, message):  # noqa: D102
        raise ContractError(message)

    def exit(self, status=0, message=None):  # noqa: D102
        if status:
            raise ContractError(message or f"exit {status}")
        raise ContractError("parser exited early (--help/--version?)")


def _literal(node):
    """能求值成字面量就返回 (True, value)，否则 (False, None)。"""
    try:
        return True, ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return False, None


def _collect_add_argument_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            calls.append(node)
    return calls


def build_parser_from_source(
        script_path: Path) -> tuple[argparse.ArgumentParser | None, list[str]]:
    """从脚本源码 AST 重建等价 parser。

    返回 (parser, 无法还原的声明说明)。脚本不用 argparse（手写 sys.argv 解析）时
    parser=None —— 这类脚本没有 argparse 契约可校验，调用方须显式跳过。
    """
    src = Path(script_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = _collect_add_argument_calls(tree)
    if not calls:
        return None, ["脚本无 argparse 声明（手写 sys.argv 解析）"]
    parser = _RaisingParser(prog=Path(script_path).name, add_help=True)
    notes: list[str] = []
    seen: set[str] = set()

    for call in calls:
        names = []
        skip = False
        for arg in call.args:
            ok, val = _literal(arg)
            if not ok or not isinstance(val, str):
                skip = True
                break
            names.append(val)
        if skip or not names:
            notes.append(f"非字面量 add_argument（跳过）· line {call.lineno}")
            continue
        key = names[0]
        if key in seen:
            # 同名选项在不同分支重复声明：argparse 会 conflict，契约上取首个即可
            continue
        seen.add(key)

        kwargs: dict = {}
        for kw in call.keywords:
            if kw.arg == "type":
                if isinstance(kw.value, ast.Name) and kw.value.id in _TYPE_MAP:
                    kwargs["type"] = _TYPE_MAP[kw.value.id]
                continue
            if kw.arg not in _KWARG_WHITELIST:
                continue
            ok, val = _literal(kw.value)
            if not ok:
                if kw.arg in ("default", "const"):
                    continue  # 非字面量默认值不影响可接受性
                notes.append(f"{key}: {kw.arg} 非字面量（按默认还原）· line {call.lineno}")
                continue
            kwargs[kw.arg] = val
        # store_true/store_false/count 不接受 type/choices/nargs
        if kwargs.get("action") in ("store_true", "store_false", "count", "help",
                                    "version"):
            for drop in ("type", "choices", "nargs", "const"):
                kwargs.pop(drop, None)
        try:
            parser.add_argument(*names, **kwargs)
        except (argparse.ArgumentError, TypeError, ValueError) as exc:
            notes.append(f"{key}: 还原失败 {exc}")
    return parser, notes


def dry_run(parser: argparse.ArgumentParser, argv: list[str]) -> str | None:
    """拿 argv 干跑 parser。兼容返回 None；不兼容返回错误串。"""
    try:
        parser.parse_args(list(argv))
    except ContractError as exc:
        return str(exc)
    except SystemExit as exc:  # 兜底：万一有未覆盖的 exit 路径
        return f"SystemExit({exc.code})"
    return None


def check_script_argv(script_path: Path, argv: list[str]) -> str | None:
    """一步到位：脚本 + argv → None(兼容 / 无 argparse 契约) / 错误串(不兼容)。"""
    parser, _notes = build_parser_from_source(script_path)
    if parser is None:
        return None
    return dry_run(parser, argv)
