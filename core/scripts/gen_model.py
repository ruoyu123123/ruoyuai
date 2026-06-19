#!/usr/bin/env python3
"""
gen_model.py — Gen-Model profile 管理 CLI

子命令：
  list              列出所有 profile + active 标记 + key 是否就绪
  show              显示 active profile 完整配置（key 脱敏）
  switch <name>     切换 active profile（原子改写 .env 中 GEN_MODEL_ACTIVE 字段）
  add <name>        在 .env 末尾追加 5 行 profile 模板（占位）

用法：
  python core/scripts/gen_model.py list
  python core/scripts/gen_model.py show
  python core/scripts/gen_model.py switch kimi_official
  python core/scripts/gen_model.py add glm_main
"""
from __future__ import annotations
import sys
import argparse
import re
from pathlib import Path

# 让本脚本可独立运行（同目录 import）
sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import GenModelLoader, GenModelConfigError  # noqa: E402


def mask_key(key: str) -> str:
    """API key 脱敏显示"""
    if not key:
        return "(未填)"
    if len(key) < 12:
        return "***"
    return key[:6] + "..." + key[-4:]


def cmd_list(args, loader: GenModelLoader) -> int:
    profiles = loader.list_profiles()
    if not profiles:
        print("(无 profile 配置；编辑 .env 添加 GEN__<name>__* 字段，或跑 gen_model.py add <name>)",
              file=sys.stderr)
        return 0
    try:
        active_name = loader.get_active_profile().name
    except GenModelConfigError:
        active_name = ""

    print(f"{'name':<22} {'model':<26} {'key':<5} active")
    print("-" * 65)
    for p in profiles:
        is_active = "★" if p.name == active_name else ""
        key_status = "✓" if p.api_key else "✗"
        print(f"{p.name:<22} {p.model:<26} {key_status:<5} {is_active}")

    chain = loader.get_fallback_chain()
    if chain:
        print(f"\nfallback chain: {','.join(chain)}")
    return 0


def cmd_show(args, loader: GenModelLoader) -> int:
    try:
        p = loader.get_active_profile()
    except GenModelConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    print(f"active profile: {p.name}")
    print(f"  model       : {p.model}")
    print(f"  base_url    : {p.base_url}")
    print(f"  api_key     : {mask_key(p.api_key)}")
    print(f"  temperature : {p.temperature}")
    print(f"  max_tokens  : {p.max_tokens or '(留空 → 从 .claude/.model_capabilities.json 取)'}")
    chain = loader.get_fallback_chain()
    print(f"  fallback    : {','.join(chain) if chain else '(无)'}")
    return 0


def set_active(loader: GenModelLoader, name: str) -> None:
    """切 active profile：dev → 原子改写 .env 的 GEN_MODEL_ACTIVE；
    dist（_dist_mode）→ 写 %APPDATA%/ruoyuai/user_overrides.env（绝不碰只读内置 config）。

    GUI runner 复用本函数。抛 ValueError/RuntimeError 由调用方处理。
    """
    from gen_model_loader import _user_override_path
    if getattr(loader, "_dist_mode", False):
        ovr = _user_override_path()
        ovr.parent.mkdir(parents=True, exist_ok=True)
        chain = ",".join(loader.get_fallback_chain())
        lines = [f"GEN_MODEL_ACTIVE={name}"]
        if chain:
            lines.append(f"GEN_MODEL_FALLBACK_CHAIN={chain}")
        ovr.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    # dev：原子改写 .env 文本
    env_path = loader.env_path
    text = env_path.read_text(encoding="utf-8")
    new_text, n = re.subn(r"^GEN_MODEL_ACTIVE\s*=.*$",
                          f"GEN_MODEL_ACTIVE={name}", text, count=1,
                          flags=re.MULTILINE)
    if n == 0:
        # 无 ACTIVE 行（极少）→ 追加一行
        new_text = text.rstrip("\n") + f"\nGEN_MODEL_ACTIVE={name}\n"
    env_path.write_text(new_text, encoding="utf-8")


def cmd_switch(args, loader: GenModelLoader) -> int:
    target = args.name
    p = loader.get_profile(target)
    if p is None:
        print(f"[ERROR] profile '{target}' 不存在；先跑 'gen_model.py add {target}' 创建模板",
              file=sys.stderr)
        print(f"  现有 profile: {[x.name for x in loader.list_profiles()]}", file=sys.stderr)
        return 2
    if not p.api_key:
        print(f"[WARN] profile '{target}' 还未填 API_KEY，切换后调用会报错", file=sys.stderr)

    set_active(loader, target)
    print(f"[OK] active = {target} ({p.model} @ {p.base_url})")
    return 0


def cmd_add(args, loader: GenModelLoader) -> int:
    name = args.name
    # 校验 name 合法性
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
        print(f"[ERROR] profile 名只能含字母/数字/下划线，且首字符必须是字母", file=sys.stderr)
        return 2
    if loader.get_profile(name):
        print(f"[ERROR] profile '{name}' 已存在", file=sys.stderr)
        return 2

    env_path = loader.env_path
    template = f"""
# ============================================================
# Profile: {name}（gen_model.py add 自动生成模板）
# 请填入 model/base_url/key，然后用 'gen_model.py switch {name}' 激活
# ============================================================
GEN__{name}__MODEL=
GEN__{name}__BASE_URL=
GEN__{name}__API_KEY=
GEN__{name}__TEMPERATURE=0.8
GEN__{name}__MAX_TOKENS=
"""
    with env_path.open("a", encoding="utf-8") as f:
        f.write(template)
    print(f"[OK] 已追加 profile 模板 '{name}' 到 .env")
    print(f"     编辑 .env 填入 model/base_url/key 后，跑：")
    print(f"     python core/scripts/gen_model.py switch {name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gen-Model profile 管理 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出所有 profile")
    sub.add_parser("show", help="显示 active profile 详情")

    p_switch = sub.add_parser("switch", help="切换 active profile")
    p_switch.add_argument("name", help="目标 profile 名")

    p_add = sub.add_parser("add", help="追加新 profile 模板到 .env")
    p_add.add_argument("name", help="新 profile 名")

    args = parser.parse_args()
    loader = GenModelLoader()

    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "switch": cmd_switch,
        "add": cmd_add,
    }
    return handlers[args.cmd](args, loader)


if __name__ == "__main__":
    sys.exit(main())
