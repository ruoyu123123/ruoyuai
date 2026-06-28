#!/usr/bin/env python3
"""scaffold_subsystems.py — 新书 34 子系统 JSON 的【确定性脚手架 + 验证】

根治「34 子系统无确定性框架→每本书手搓/agent 自由生成→契约债 + 弱模型翻车」。
单一真理源 = core/claude-home/templates/subsystem_skeletons.json（schema 正确的空骨架）。
弱模型只需往骨架里填内容，不碰 schema（照顾弱模型）。

用法：
  python scaffold_subsystems.py --list                       # 列出 34 个 canonical 文件
  python scaffold_subsystems.py emit  <项目名|--db-dir 路径>  # 生成缺失骨架（已存在不覆盖，--force 覆盖）
  python scaffold_subsystems.py verify <项目名|--db-dir 路径>  # 校验 34 件存在 + json 合法（流程缺步补全）
      [--consumption]                                          # 🔴 P0-05: 交叉自检 _doc.consumption 字段（骨架→消费方）
      [--strict-extras]                                        # 🔴 P2-12: 顶层非 canonical 34 + 非 KNOWN_EXTRAS 白名单 .json 给警告
      [--content]                                              # 🔴 C03: 载荷内容验收·载荷文件空(inert/hard·exit2)·非载荷裸骨架(bare/advisory·exit0)
      [--shallow-drift]                                        # 🔴 C17: 高级子系统顶层结构浅漂移哨兵(advisory·永不 exit 非0·永不写文件)

退出码：
  0  成功 / 全部齐全合法（含 --content 仅裸骨架 advisory）
  2  verify 发现缺失或损坏 / --content 载荷空货架(inert) / 致命错误
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # 仓库根（dev）
# 🔴 frozen-aware（对抗审查 must_fix·__file__ 扁平化同款）：只读骨架资源改用 bundle_root()
# （frozen=_MEIPASS·dev=parents[2] 逐字节一致·datas 落 bundle_root()/core/claude-home/
# templates）——否则 frozen 下 /outline scaffold 读不到 subsystem_skeletons.json。
try:
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from frozen_util import bundle_root as _bundle_root
    SKELETON_FILE = _bundle_root() / "core" / "claude-home" / "templates" / "subsystem_skeletons.json"
except Exception:
    SKELETON_FILE = ROOT / "core" / "claude-home" / "templates" / "subsystem_skeletons.json"


def _load_skeletons():
    data = json.loads(SKELETON_FILE.read_text(encoding="utf-8"))
    return data["_canonical_34"], data["skeletons"]


# 🔴 2026-06-27 P2-12：scaffold 顶层 .json 白名单（非 canonical 34 但允许存在·避免 --strict-extras 误报）。
# triage_band_report.json = band 自校准运行时报告（已 .gitignore）·character_index = scanner 专用索引。
KNOWN_EXTRAS = {
    "character_index.json",
    "triage_band_report.json",
    # 🔴 2026-06-29 角色信息差(per-character belief)：辅助态文件·非 34 核心·像 locked_fact.json·
    # 由 world_seed_init 播空骨架 + cluster-save-state witness 回写维护·不进 canonical 循环。
    "character_belief_ledger.json",
    "locked_fact.json",
    # 🔴 2026-06-29 戏剧问题账本(PITQ/MDQ)：辅助态文件·非 34 核心·像 character_belief_ledger.json·
    # 由 world_seed_init 播空骨架 + cluster-save-state foreshadower 登记步维护·不进 canonical 循环。
    "戏剧问题账本.json",
}

# 🔴 2026-06-27 C03/C17：载荷非空标记 key（skeleton 内·单一真理源）。
# 仅 3 个「机器永不点火」载荷文件带此标记：涟漪规则(引擎零触发)/大势卡(大势无方向)/
# 事件簇(cluster_001 storyboard 必详化)。其余 31 个无标记 = fluid-allowed 裸骨架永远 advisory。
LOAD_BEARING_KEY = "_load_bearing_nonempty"

# 🔴 2026-06-27 C17：高级 16 子系统（基础 18 之外·允许最小骨架占位但文件必存）。
# 与 plan_step_gates.REQUIRED_DB_FILES「高级-*」分类一一对应（基础人物世界5+叙事7+风格质控4+
# 世界演化2=18；高级 Hub/Clock/Storyteller/Stress4+角色弧线NPC3+事件池2+蒸馏2+长篇工具5=16）。
# 浅漂移哨兵只扫这 16 个（fluid 涌现最可能在高级层动顶层结构）·全 advisory 永不阻断。
ADVANCED_SUBSYSTEMS = (
    "枢纽场景", "时钟表", "叙事节拍器", "主角压力档",
    "character_arc_state", "角色行动表", "群像档",
    "事件池", "行动判定模板",
    "角色池", "角色烙印",
    "knowledge_graph", "subplot_threads", "beat_map", "四线脉络",
    "webnovel_bench_mapping",
)


# 🔴 2026-06-27 C03 载荷非空判定（单一真理源·verify --content 与 plan_step_gates 共用）
def _resolve_dotpath(obj, dotpath):
    """按 dot-path 取值，支持 list 整数索引（如 clusters.0.scene_storyboard）。缺路径返回 None。"""
    cur = obj
    for part in str(dotpath).split("."):
        if isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if not (0 <= idx < len(cur)):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def _is_nonempty(v) -> bool:
    """list/dict/str 看长度；数值/bool 视为「有值」；None 视为空。"""
    if v is None:
        return False
    if isinstance(v, (list, dict, str)):
        return len(v) > 0
    return True


def eval_load_bearing(marker: dict, obj) -> tuple:
    """marker={code, any_of:[dotpath,...]} → (code, ok)。any_of 任一路径非空即 ok。"""
    code = marker.get("code", "LOAD_BEARING_EMPTY")
    paths = marker.get("any_of") or marker.get("paths") or []
    ok = any(_is_nonempty(_resolve_dotpath(obj, p)) for p in paths)
    return code, bool(ok)


def _resolve_db_dir(args) -> Path:
    """从 args 解析 _数据库 目录。支持四种写法：
    --db-dir <路径> / 位置参数=项目名 / 项目根路径 / 直接 _数据库 路径。
    （cluster-save-state 传的是项目路径，/outline 传的是项目名，都要兼容）"""
    if "--db-dir" in args:
        i = args.index("--db-dir")
        return Path(args[i + 1])
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("[FATAL] 需要 项目名 / 项目路径 / --db-dir 路径", file=sys.stderr)
        sys.exit(2)
    arg = positional[0]
    p = Path(arg)
    # ① 已经指到 _数据库 目录
    if p.name == "_数据库":
        return p
    # ② 是个路径（含分隔符 或 真实存在的目录）→ 当项目根，拼 _数据库
    if (os.sep in arg) or ("/" in arg) or p.exists():
        return p / "_数据库"
    # ③ 纯名字 → 当项目名，在 workspace/novels|styles 下找
    for base in ("novels", "styles"):
        cand = ROOT / "workspace" / base / arg / "_数据库"
        if cand.parent.exists():
            return cand
    return ROOT / "workspace" / "novels" / arg / "_数据库"


def _atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def cmd_list():
    canonical, _ = _load_skeletons()
    print(f"canonical 34 子系统（{len(canonical)} 个）:")
    for i, name in enumerate(canonical, 1):
        print(f"  {i:2d}. {name}.json")
    return 0


def cmd_emit(args):
    canonical, skeletons = _load_skeletons()
    db = _resolve_db_dir(args)
    force = "--force" in args
    db.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for name in canonical:
        path = db / f"{name}.json"
        if path.exists() and not force:
            skipped.append(name)
            continue
        skel = skeletons.get(name)
        if skel is None:
            print(f"[WARN] skeletons.json 缺 {name} 骨架，写空对象兜底", file=sys.stderr)
            skel = {"schema_version": "v27", "_doc": f"{name} 骨架占位（skeletons.json 未定义）"}
        _atomic_write(path, skel)
        created.append(name)
    print(f"[scaffold emit] 目标: {db}")
    print(f"  新建 {len(created)} · 跳过(已存在) {len(skipped)} · 共 {len(canonical)}")
    if created:
        print(f"  新建: {created}")
    if skipped and force:
        print("  (--force 模式应全部覆盖，无跳过)")
    return 0


def cmd_verify(args):
    canonical, skeletons = _load_skeletons()
    db = _resolve_db_dir(args)
    if not db.exists():
        print(f"[FATAL] _数据库 目录不存在: {db}", file=sys.stderr)
        return 2
    missing, bad, no_schema_ver, ok = [], [], [], 0
    for name in canonical:
        path = db / f"{name}.json"
        if not path.exists():
            missing.append(name)
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append((name, str(e)[:80]))
            continue
        ok += 1
        if isinstance(obj, dict) and "schema_version" not in obj and "_schema" not in obj:
            no_schema_ver.append(name)
    print(f"[scaffold verify] 目标: {db}")
    print(f"  合法 {ok}/{len(canonical)} · 缺失 {len(missing)} · 损坏 {len(bad)} · 无 schema_version {len(no_schema_ver)}")
    if missing:
        print(f"  ❌ 缺失: {missing}", file=sys.stderr)
    if bad:
        print(f"  ❌ 损坏: {bad}", file=sys.stderr)
    if no_schema_ver:
        print(f"  ⚠ 无 schema_version(建议补): {no_schema_ver}")
    # 🔴 P0-05: --consumption 交叉自检（骨架 _doc.consumption 字段是否齐 + status 分布）
    if "--consumption" in args:
        no_consumption = []
        layer_dist = {"direct_inject": 0, "via_scanner": 0, "via_engine": 0,
                      "deferred": 0, "DEAD": 0, "unknown": 0}
        status_dist = {"live": 0, "deferred": 0, "unknown": 0}
        for name in canonical:
            skel = skeletons.get(name) or {}
            cons = skel.get("consumption") if isinstance(skel, dict) else None
            if not isinstance(cons, dict):
                no_consumption.append(name)
                layer_dist["unknown"] += 1
                status_dist["unknown"] += 1
                continue
            layer = cons.get("layer", "unknown")
            status = cons.get("status", "unknown")
            layer_dist[layer] = layer_dist.get(layer, 0) + 1
            status_dist[status] = status_dist.get(status, 0) + 1
        print(f"[scaffold verify --consumption] layer 分布: {layer_dist}")
        print(f"  status 分布: {status_dist}")
        if no_consumption:
            print(f"  ⚠ 无 _doc.consumption(建议补): {no_consumption}")
    # 🔴 P2-12: --strict-extras 顶层非 canonical 34 + 非 KNOWN_EXTRAS 白名单 .json 给警告
    if "--strict-extras" in args:
        canonical_files = {f"{n}.json" for n in canonical}
        try:
            extras = sorted(p.name for p in db.glob("*.json")
                            if p.name not in canonical_files
                            and p.name not in KNOWN_EXTRAS)
        except OSError:
            extras = []
        if extras:
            print(f"  ⚠ 顶层 .json 非 canonical 34 + 非 KNOWN_EXTRAS 白名单: {extras}",
                  file=sys.stderr)
        else:
            print("  ✅ --strict-extras: 顶层 .json 全在白名单内")
    # 🔴 2026-06-27 C03: --content 载荷内容验收（治「34 建齐」只验文件存在·空货架蒙混）
    #   (a) 与 skeleton 语义相等 → bare_skeleton(advisory·非载荷 fluid 合法)
    #   (b) 载荷文件按 _load_bearing_nonempty 标记查非空·空 → inert(hard·机器永不点火)
    #   仅 bare → exit0+advisory · 有 inert → exit2
    content_inert = False
    if "--content" in args:
        bare, inert = [], []
        for name in canonical:
            path = db / f"{name}.json"
            if not path.exists():
                continue
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue  # 损坏已在上面 bad 报
            skel = skeletons.get(name) or {}
            marker = skel.get(LOAD_BEARING_KEY) if isinstance(skel, dict) else None
            if isinstance(marker, dict):
                code, ok = eval_load_bearing(marker, obj)
                if not ok:
                    inert.append((name, code))
            elif obj == skel:
                bare.append(name)
        print(f"[scaffold verify --content] 载荷非空验收: "
              f"inert(hard) {len(inert)} · 裸骨架(advisory) {len(bare)}")
        if bare:
            print(f"  ℹ 非载荷裸骨架(advisory·fluid 合法·待 outline/用户填): {bare}")
        if inert:
            content_inert = True
            print(f"  ❌ 载荷空货架(inert·hard·机器永不点火): "
                  f"{[f'{n}:{c}' for n, c in inert]}", file=sys.stderr)
            print("     涟漪规则空=引擎零触发 / 当前卷 ME 池空=大势无方向 / "
                  "cluster_001 storyboard 空=首块未详化", file=sys.stderr)
            print("     修复: 让 outline 真正填充载荷内容（非仅建空骨架）·或轻量模式旁路 "
                  ".subsystems_bypass.json", file=sys.stderr)
        else:
            print("  ✅ --content: 所有载荷文件非空（机器可点火）")
    # 🔴 2026-06-27 C17: --shallow-drift 高级 21(此处 16)子系统浅漂移哨兵（advisory·永不 exit 非0·永不写文件）
    if "--shallow-drift" in args:
        cmd_shallow_drift(db, skeletons)
    if missing or bad or content_inert:
        return 2
    return 0


# 🔴 2026-06-27 C17: 浅漂移哨兵 —— 只查高级子系统顶层 key 结构形态（advisory-only）。
#   (a) skeleton 有且 consumption.status=='live' 但项目缺 → ⚠ MISSING_LIVE_KEY
#   (b) 项目有 skeleton 无 → ℹ UNKNOWN_TOP_KEY（fluid 涌现可能合法新增·仅 info）
#   (c) consumption.status 非 live → 跳过
# 永不 block / 永不 exit 非0 / 永不写文件。接 cluster-save-state validate 步调一次
# （plan 接线注释：cluster-save-state.plan.json 的 validate step scripts 追加
#  `python core/scripts/scaffold_subsystems.py verify <project> --shallow-drift`·advisory 不影响 step 通过）。
def cmd_shallow_drift(db: Path, skeletons: dict) -> None:
    def _content_keys(d: dict) -> set:
        # 排 _doc/consumption/schema_version（及一切 _ 前缀 meta：_schema/_metadata/_me_schema…）
        return {k for k in d
                if not k.startswith("_") and k not in ("consumption", "schema_version")}

    print("[scaffold verify --shallow-drift] 高级子系统顶层结构漂移哨兵（advisory·永不阻断）:")
    drift_n = 0
    for name in ADVANCED_SUBSYSTEMS:
        skel = skeletons.get(name) or {}
        if not isinstance(skel, dict):
            continue
        cons = skel.get("consumption") or {}
        status = cons.get("status", "unknown")
        if status != "live":
            print(f"  · {name}: consumption.status={status}（非 live·跳过）")
            continue
        path = db / f"{name}.json"
        if not path.exists():
            continue  # 缺失由 existence 检查报·哨兵不重复
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        canon_keys = _content_keys(skel)
        proj_keys = _content_keys(obj)
        for k in sorted(canon_keys - proj_keys):
            drift_n += 1
            print(f"  ⚠ MISSING_LIVE_KEY {name}.{k} "
                  f"(by={cons.get('by', [])}·skeleton live 顶层 key·项目缺·建议补)")
        for k in sorted(proj_keys - canon_keys):
            drift_n += 1
            print(f"  ℹ UNKNOWN_TOP_KEY {name}.{k} "
                  f"(项目有·skeleton 无·fluid 涌现可能合法新增·仅 info)")
    if drift_n == 0:
        print("  ✅ 高级子系统顶层结构与 skeleton canonical 一致（零漂移）")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if "--list" in args or args[0] == "list":
        return cmd_list()
    op = args[0]
    rest = args[1:]
    if op == "emit":
        return cmd_emit(rest)
    if op == "verify":
        return cmd_verify(rest)
    print(f"[FATAL] 未知操作: {op}（用 emit / verify / list）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
