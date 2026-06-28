"""export_book.py — 全书导出脚本（缺漏修复批次1 · P0-1 核心件）

【缺漏报告结论】
  P0-1：/export 命令文档存在但**没有可执行导出脚本** —— 主代理每次手写拼接逻辑，
        口径不一（标题来源 / CHANGES 剥离 / 缺章处理各凭手感）。本脚本是唯一权威导出入口。
  P1-2：v27 splitter 末章 < 3000 CJK 时把尾段退回 pending_tail，全书最后一个 cluster
        的 pending_tail 永远没有后继来拼 → 导出会**静默丢失正文**。导出前必须接线
        finalize_book 的孤儿检测（advisory：告警不阻断，北极星⑤ 顾问非法官）。

【功能】
  把 workspace/novels/<书>/章节/第NNN章/第NNN章.txt 按章号升序拼接成全文：
    - 每章前插「第N章 标题」行（标题读 第N章_changes.json 的 title · 空则只写「第N章」）
    - 正文自带「第N章 …」标题行时摘出去重（splitter 历史上把标题留在正文里）
    - 章间空行分隔
    - 正文混入 ---CHANGES--- 标记段 → 剥掉（保守防御 · 复用 chapter_io 分隔符口径）
    - 某章 txt 缺失 → stderr WARN 跳过，不中断
  输出：<project_root>/exports/<书名>_全文_<章数>章.txt（utf-8 · exports/ 不存在则建）

【CLI】
  python core/scripts/export_book.py <project_root> [--out <path>]
  退出码：0 成功 / 1 无任何章节可导出 / 2 项目路径不存在

【章节为格式层】（北极星④）
  本脚本只做格式拼接，不做任何质检/状态/学习 —— pending_tail 检测也只是 advisory 告警。
"""

import sys
import json
import re
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio  # noqa: E402
import finalize_book as fb  # noqa: E402  # P1-2 接线：复用其孤儿检测，不自己重造

# 章节正文文件名 / 章节目录名 / 正文首行标题
_CH_FILE_RE = re.compile(r"^第(\d+)章\.txt$")
_CH_DIR_RE = re.compile(r"^第(\d+)章$")
_TITLE_LINE_RE = re.compile(r"^第(\d+)章\s*(.*)$")


# ============ 章节发现 ============

def _is_excluded(f: Path, project_root: Path) -> bool:
    """章节正文是否落在非正典区（`_` 前缀目录 / *_draft 目录）→ 不参与导出。

    排除：任何 `_` 前缀目录（_archived_v1 / _tmp / _数据库 …）+ *_draft 目录
    （cluster 草稿目录里的中间产物绝不能混进导出）。
    """
    try:
        rel_parts = f.relative_to(project_root).parts
    except ValueError:
        rel_parts = f.parts
    if any(p.startswith("_") for p in rel_parts[:-1]):
        return True
    if f.parent.name.endswith("_draft"):
        return True
    return False


def scan_chapter_sources(project_root) -> dict:
    """扫描所有候选章节正文（未折叠），返回 {章号(int): [正文 Path, ...]}。

    🔴 2026-06-27 W5：用于全书完整性自检的「同章号重复源」检测——一个章号对应
    2+ 个正典源文件 = 数据完整性异常（hard）。物理文件按 resolve() 去重（root 基址
    rglob 会重复扫到 章节/ 下的同一文件 · 不去重会把每章误判为重复）。
    """
    project_root = Path(project_root)
    multimap: dict = {}
    seen: set = set()
    bases = []
    if (project_root / "章节").is_dir():
        bases.append(project_root / "章节")
    bases.append(project_root)  # 平铺旧布局兜底
    for base in bases:
        if not base.is_dir():
            continue
        for f in base.rglob("第*章.txt"):
            try:
                rp = f.resolve()
            except OSError:
                rp = f
            if rp in seen:
                continue
            seen.add(rp)
            if _is_excluded(f, project_root):
                continue
            m = _CH_FILE_RE.match(f.name)
            if not m:
                continue
            multimap.setdefault(int(m.group(1)), []).append(f)
    return multimap


def discover_chapters(project_root) -> dict:
    """扫描章节正文文件，返回 {章号(int): 正文 Path}（每章取首个候选）。

    覆盖布局：章节/第NNN章/第NNN章.txt（标准）+ 平铺旧布局（项目根直放）。
    首个候选 = 基址顺序（章节/ 优先于平铺根）下最先发现的物理文件，与历史
    `setdefault` 口径一致。重复源检测见 scan_chapter_sources。
    """
    return {ch: paths[0] for ch, paths in scan_chapter_sources(project_root).items()}


def find_missing_chapter_dirs(project_root, found: dict) -> list:
    """章节/第N章/ 目录存在但正文 txt 缺失的章号列表（用于 WARN 跳过，不中断）。"""
    base = Path(project_root) / "章节"
    missing = []
    if not base.is_dir():
        return missing
    for d in base.iterdir():
        m = _CH_DIR_RE.match(d.name)
        if d.is_dir() and m:
            ch = int(m.group(1))
            if ch not in found:
                missing.append(ch)
    return sorted(missing)


# ============ 标题 ============

def read_title(project_root, ch: int) -> str:
    """读 第N章_changes.json 的 title。

    实地验证（凿窍纪 第001章_changes.json）：title 在**顶层**；防御性兼容
    factual.title / self_eval.title（LLM 自由 schema 历史教训 · consumer tolerant）。
    缺文件 / 解析失败 / 空值 → 返回 ''（导出只写「第N章」）。
    """
    cp = cio.changes_path(project_root, ch)
    if not cp.is_file():
        d = cio.find_chapter_dir(project_root, ch)
        if d:
            for cand in (d / f"第{ch:03d}章_changes.json", d / f"第{ch}章_changes.json"):
                if cand.is_file():
                    cp = cand
                    break
    if not cp.is_file():
        return ""
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for holder in (data, data.get("factual"), data.get("self_eval")):
        if isinstance(holder, dict):
            t = holder.get("title")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""


# ============ 正文处理 ============

def _strip_changes_defensive(text: str) -> str:
    """剥掉混入正文的 CHANGES 标记段（保守防御）。

    复用 chapter_io 的分隔符口径（CHANGES_SEPARATORS + SELF_EVAL_SEP），
    任一标记后的内容全部丢弃 —— 导出文件里绝不能出现机器数据段。
    """
    for sep in cio.CHANGES_SEPARATORS + (cio.SELF_EVAL_SEP,):
        if sep in text:
            text = text.split(sep)[0]
    return text.rstrip()


def build_chapter_parts(ch: int, raw_text: str, title: str) -> tuple:
    """拆单章为 (标题行 header, 纯正文 body)。

    正文自带「第N章 …」标题行 → 摘出（避免双标题）；其标题文本可作
    changes.json title 缺失时的兜底。🔴 2026-06-27 W5：拆出 header/body 供
    全书字数守恒自检分别计 CJK（标题行 = 固定开销，正文 = 内容）。
    """
    body = _strip_changes_defensive(raw_text)
    lines = body.split("\n")
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    body_title = ""
    if idx < len(lines):
        m = _TITLE_LINE_RE.match(lines[idx].strip())
        if m:
            body_title = m.group(2).strip()
            lines = lines[idx + 1:]
    body = "\n".join(lines).strip("\n")
    t = title or body_title
    header = f"第{ch}章 {t}" if t else f"第{ch}章"
    return header, body.strip()


def build_chapter_block(ch: int, raw_text: str, title: str) -> str:
    """拼单章块：「第N章 标题」行 + 空行 + 纯正文（= build_chapter_parts 的拼接形态）。"""
    header, body = build_chapter_parts(ch, raw_text, title)
    return header + "\n\n" + body


# ============ P1-2：pending_tail 孤儿检测接线 ============

def check_pending_tail_orphans(project_root) -> list:
    """导出前调 finalize_book.scan_pending_tails 检孤儿尾段。

    缺漏报告 P1-2：export 只拼 第N章.txt，扫不到 draft 目录里的 pending_tail，
    正文会被静默丢弃。这里 advisory 告警（stderr 醒目）但**仍继续导出**——
    顾问非法官（北极星⑤），且检测自身异常也不许中断导出。
    """
    try:
        orphans = fb.scan_pending_tails(Path(project_root))
    except Exception as e:  # 检测层故障绝不拖垮导出主流程
        print(f"[WARN] pending_tail 孤儿检测失败（不影响导出）: {e}", file=sys.stderr)
        return []
    if orphans:
        total = sum(o.get("cjk", 0) for o in orphans)
        print("=" * 64, file=sys.stderr)
        print(f"[⚠ 警告] 检出 {len(orphans)} 个未拼接的 pending_tail 孤儿，"
              f"最后一块有 ~{total} 字尾段未入章 · 本次导出不含这些内容！", file=sys.stderr)
        for o in orphans:
            tag = "（全书最后 cluster · 无后继可拼）" if o.get("is_last_cluster") else "（后继 cluster 漏拼）"
            print(f"  · cluster_{o['cluster_key']}  ~{o['cjk']} 字  {o['path']}  {tag}", file=sys.stderr)
        print("  建议：先跑 cluster-write 下一块拼接，或 "
              "python core/scripts/finalize_book.py <项目> --flush append/split", file=sys.stderr)
        print("=" * 64, file=sys.stderr)
    return orphans


# ============ 🔴 2026-06-27 W5：全书结构完整性自检（北极星④ 只查结构·不查内容质量）============
#
# 对齐 C18 splitter 守恒哲学，但作用在**全书层面**。export 是「最终交付物」——
# 章节缺失/重复/顺序错乱/跨 cluster 字数丢失此前完全无人守（silently 失败）。
# 这里加确定性自检：缺章/重复 = hard（数据丢失·--strict 下 exit 2）·字数偏差/覆盖
# 缺口 = advisory。**绝不断言内容质量/风格/叙事**——export 是格式层（北极星④）。

def _load_cluster_coverage(project_root) -> set:
    """汇总 事件簇.json + cluster_blueprint 所有 cluster 的 chapter_range 并集（章号集合）。

    无任何可用 range（fluid 未回填 / 文件缺失 / cluster_lookup 不可用）→ 返回 None
    （coverage 检查跳过 · advisory，绝不因此中断导出）。
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import cluster_lookup as cl  # noqa: WPS433
        covered: set = set()
        for _cid, rng in cl._iter_event_cluster_ranges(project_root):
            if rng:
                covered.update(range(rng[0], rng[1] + 1))
        if not covered:  # 事件簇未回填 → blueprint 兜底
            for _cid, rng, _sb in cl._iter_blueprint_ranges(project_root):
                if rng:
                    covered.update(range(rng[0], rng[1] + 1))
        return covered or None
    except Exception:
        return None


def check_book_integrity(project_root, parts_by_ch: dict, full_text: str) -> dict:
    """拼接后全书结构完整性自检（确定性 · 只查结构 · 北极星④）。

    parts_by_ch: {章号: (header, body, block)}（已导出的章 · 升序拼接源）。
    三查：① 章号连续性（1..max 无缺号 + 同章号无重复源） ② 升序 ③ 跨 cluster 字数守恒
    （sum(各章 CJK) == 拼接全书 CJK · 标题行作固定开销分项计）。
    缺章/重复/乱序 = hard（数据丢失）· 字数偏差/覆盖缺口 = advisory。
    """
    exported = sorted(parts_by_ch.keys())
    issues = []

    # ① 连续性：1..max 内的缺号（最强数据丢失信号——线性书不可能有 N 而无 N-1）
    missing = []
    if exported:
        present = set(exported)
        missing = [c for c in range(min(exported), max(exported) + 1) if c not in present]

    # ① 重复：同章号 2+ 个正典源文件（数据完整性异常 · 导出取首个 → 另一份静默丢失）
    try:
        multimap = scan_chapter_sources(project_root)
        duplicates = {ch: [str(p) for p in paths]
                      for ch, paths in multimap.items() if len(paths) > 1}
    except Exception:
        duplicates = {}

    # ② 升序（导出已 sorted · 防御性核对·恒 True，留作未来重构哨兵）
    ascending = (exported == sorted(exported))

    # ③ 字数守恒：full = "\n\n".join(blocks) + "\n"（分隔符为换行·0 CJK）→
    #    full_cjk 应恒等于 sum(各章 header_cjk + body_cjk)·diff != 0 即结构异常
    body_cjk = sum(cio.count_cjk(b) for (_h, b, _bl) in parts_by_ch.values())
    header_cjk = sum(cio.count_cjk(h) for (h, _b, _bl) in parts_by_ch.values())
    full_cjk = cio.count_cjk(full_text)
    cons_diff = full_cjk - (body_cjk + header_cjk)

    # coverage（advisory · best-effort · cluster range 交叉核对）
    covered = _load_cluster_coverage(project_root)
    coverage = {"available": covered is not None, "uncovered": [], "extra": []}
    if covered is not None and exported:
        present = set(exported)
        coverage["uncovered"] = sorted(covered - present)   # range 声明但未导出
        coverage["extra"] = sorted(present - covered)        # 导出但无 cluster 声明（fluid 常见）

    if missing:
        issues.append({"code": "CHAPTER_MISSING", "level": "hard", "detail": missing})
    if duplicates:
        issues.append({"code": "CHAPTER_DUPLICATE", "level": "hard",
                       "detail": sorted(duplicates.keys())})
    if not ascending:
        issues.append({"code": "CHAPTER_OUT_OF_ORDER", "level": "hard", "detail": exported})
    if cons_diff != 0:
        issues.append({"code": "WORD_CONSERVATION_DRIFT", "level": "advisory", "detail": cons_diff})
    if coverage["uncovered"]:
        issues.append({"code": "CLUSTER_RANGE_UNCOVERED", "level": "advisory",
                       "detail": coverage["uncovered"]})

    has_hard = any(i["level"] == "hard" for i in issues)
    has_adv = any(i["level"] == "advisory" for i in issues)
    verdict = "hard" if has_hard else ("advisory" if has_adv else "ok")
    return {
        "verdict": verdict,
        "ok": not has_hard,
        "exported_chapters": exported,
        "continuity": {
            "expected_range": [min(exported), max(exported)] if exported else [],
            "missing": missing,
            "duplicates": duplicates,
            "ascending": ascending,
        },
        "coverage": coverage,
        "word_conservation": {
            "body_content_cjk": body_cjk,
            "title_overhead_cjk": header_cjk,
            "full_cjk": full_cjk,
            "diff": cons_diff,
            "ok": cons_diff == 0,
        },
        "issues": issues,
    }


def _print_integrity_report(integrity: dict, strict: bool) -> None:
    """完整性自检结果 → stderr。缺章/重复/乱序：strict→[FATAL]·默认→[WARN]
    （默认 advisory 不打 [FATAL]·避免污染 runtime_monitor 误报真故障）。"""
    if integrity["verdict"] == "ok":
        return
    cont = integrity["continuity"]
    miss, dup = cont["missing"], cont["duplicates"]
    wc, cov = integrity["word_conservation"], integrity["coverage"]
    print("=" * 64, file=sys.stderr)
    if miss or dup or not cont["ascending"]:
        tag = "[FATAL]" if strict else "[WARN]"
        bits = []
        if miss:
            bits.append(f"缺失章节 {miss}")
        if dup:
            bits.append(f"重复章节 {sorted(dup.keys())}")
        if not cont["ascending"]:
            bits.append("章号非升序")
        suffix = "（--strict · exit 2）" if strict else "（advisory · 导出照常 · 硬门禁加 --strict）"
        print(f"{tag} 全书完整性破损：{' / '.join(bits)} {suffix}", file=sys.stderr)
        for ch, paths in dup.items():
            print(f"    第{ch}章 重复源: {paths}", file=sys.stderr)
    if not wc["ok"]:
        print(f"[WARN] 字数守恒偏差 diff={wc['diff']}（正文 {wc['body_content_cjk']} + 标题 "
              f"{wc['title_overhead_cjk']} vs 全书 {wc['full_cjk']} CJK · advisory）", file=sys.stderr)
    if cov["available"] and cov["uncovered"]:
        print(f"[WARN] cluster 覆盖缺口：range 声明但未导出 {cov['uncovered']}"
              f"（advisory · 可能 fluid 未回填 / 尚未写）", file=sys.stderr)
    print("=" * 64, file=sys.stderr)


# ============ 导出主流程 ============

def build_compliance_checklist() -> str:
    """发布前合规自查清单（advisory·静态·复用 D2 合规护城河 feedback_reader_growth_compliance_redline·
    守机器产草稿人做发布决策·不替用户上传）。"""
    return """# 📋 发布前合规自查清单
> 本工具只产成品·**不替你上传**——请手动登录平台合规投稿。机器产草稿·发布决策你来做。

## ⚠️ 平台 AI 政策（2026 严打 AI 铺量·投稿前先看）
- [ ] 已查目标平台最新「AI 创作披露政策」（番茄/起点/七猫/晋江各不同·部分要求显式标注 AI 参与）
- [ ] 确认单本精写·非批量起号（平台 2026 已清低质违规 4 万+本·一身份证一账号·铺量=重点打击画像）

## 📐 格式与内容规范
- [ ] 字数 / 分章符合目标平台规范（章节长度·卷结构）
- [ ] 标题 / 简介 / 标签无违禁词
- [ ] 敏感内容已自查（涉政 / 暴力 / 色情按平台尺度）

## 🚫 红线（绝不做·封号风险）
- [ ] 不用一键自动发布 / 模拟登录 / Cookie 复用分发（违 ToS）
- [ ] 不用「降 AI 率 / 过检」工具（猫鼠游戏·法律 + 政策风险）
- [ ] 不跨平台铺量起号 / 书评区群发轰炸

## ✅ 发布动作（你手动做）
- [ ] 登录平台官方作家后台·手动提交
- [ ] 按平台要求填 AI 参与披露（如适用）
"""


def export_book(project_root, out_path=None, with_adaptation_kit=False,
                with_compliance_checklist=True, strict=False) -> dict:
    """拼接全书。成功返回报告 dict；无任何章节可导出返回 None。

    with_adaptation_kit=True（--adaptation-kit）：导出后额外产改编资料包（一人公司·喂 IP 后端·
    人物小传/世界设定集/故事梗概/高潮伏笔清单·纯确定性投影）。默认 False 零回归。

    🔴 2026-06-27 W5：拼接后跑 check_book_integrity 全书结构自检（缺章/重复/乱序/字数守恒），
    结果写 report['integrity']。strict 只影响 stderr 标签（[FATAL] vs [WARN]）与 main 退出码——
    export_book 本身**永远完成导出并返回报告**（顾问非法官 · 北极星④/⑤）。
    """
    project_root = Path(project_root).resolve()
    found = discover_chapters(project_root)
    if not found:
        print(f"[FATAL] 未找到任何章节正文（章节/第N章/第N章.txt）: {project_root}", file=sys.stderr)
        return None

    # P1-2：导出前孤儿检测（advisory · 不阻断）
    orphans = check_pending_tail_orphans(project_root)

    # 目录在但 txt 缺 → WARN 跳过
    for ch in find_missing_chapter_dirs(project_root, found):
        print(f"[WARN] 第{ch}章 目录存在但正文 txt 缺失 · 跳过不中断", file=sys.stderr)

    blocks = []
    exported = []
    parts_by_ch = {}
    for ch in sorted(found):
        try:
            raw = found[ch].read_text(encoding="utf-8")
        except Exception as e:
            print(f"[WARN] 第{ch}章 正文读取失败 · 跳过: {e}", file=sys.stderr)
            continue
        header, body = build_chapter_parts(ch, raw, read_title(project_root, ch))
        block = header + "\n\n" + body
        parts_by_ch[ch] = (header, body, block)
        blocks.append(block)
        exported.append(ch)
    if not blocks:
        print("[FATAL] 所有章节均读取失败，无内容可导出", file=sys.stderr)
        return None

    full = "\n\n".join(blocks) + "\n"  # 章间空行

    # 🔴 2026-06-27 W5：全书结构完整性自检（缺章/重复/乱序/字数守恒 · 只查结构不查内容）
    integrity = check_book_integrity(project_root, parts_by_ch, full)
    _print_integrity_report(integrity, strict)
    if out_path is None:
        out_dir = project_root / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{project_root.name}_全文_{len(blocks)}章.txt"
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full, encoding="utf-8")

    # 改编资料包（一人公司·IP 后端·可选 --adaptation-kit·默认不产零回归·失败不影响主导出）
    adaptation = None
    if with_adaptation_kit:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            import adaptation_kit
            adaptation = adaptation_kit.generate_kit(project_root)
            print(f"[OK] 改编资料包：{len(adaptation.get('written', {}))} 份 → {adaptation.get('out_dir')}")
        except Exception as e:  # noqa: BLE001 · 改编资料包失败不影响主导出
            print(f"[WARN] 改编资料包生成失败（不影响导出）: {str(e)[:120]}", file=sys.stderr)

    # 发布前合规自查清单（一人公司·D2 合规护城河·默认产·advisory·失败不影响主导出）
    compliance_path = None
    if with_compliance_checklist:
        try:
            compliance_path = out_path.parent / "发布前合规自查.md"
            compliance_path.write_text(build_compliance_checklist(), encoding="utf-8")
            print(f"[OK] 发布前合规自查清单 → {compliance_path}")
        except Exception as e:  # noqa: BLE001 · 合规清单失败不影响主导出
            print(f"[WARN] 合规自查清单生成失败（不影响导出）: {str(e)[:120]}", file=sys.stderr)
            compliance_path = None

    report = {
        "out_path": str(out_path),
        "chapters": len(blocks),
        "chapter_list": exported,
        "total_words": cio.count_words(full),
        "total_cjk": cio.count_cjk(full),
        "orphan_pending_tails": len(orphans),
        "integrity": integrity,  # 🔴 2026-06-27 W5：全书结构完整性自检段
        "adaptation_kit": adaptation,
        "compliance_checklist": str(compliance_path) if compliance_path else None,
    }
    print(f"[OK] 导出完成：{len(blocks)} 章 / {report['total_cjk']} CJK"
          f" · 完整性 {integrity['verdict']} → {out_path}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="全书导出：章节按章号升序拼接成全文 txt")
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--out", default=None, help="输出文件路径（默认 <项目>/exports/<书名>_全文_<章数>章.txt）")
    ap.add_argument("--adaptation-kit", action="store_true",
                    help="同时产改编资料包(人物小传/设定集/梗概/高潮清单·喂 IP 后端·纯确定性投影)")
    ap.add_argument("--strict", action="store_true",
                    help="🔴 W5：全书完整性 hard 破损(缺章/重复/乱序)时 exit 2(默认 advisory·exit 0·绝不阻断导出)")
    args = ap.parse_args(argv)

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {args.project}", file=sys.stderr)
        sys.exit(2)

    report = export_book(project_root, args.out, with_adaptation_kit=args.adaptation_kit,
                         strict=args.strict)
    if report is None:
        sys.exit(1)
    # 🔴 2026-06-27 W5：--strict 下 hard 完整性破损(数据丢失) → exit 2；默认顾问制 exit 0
    if args.strict and report.get("integrity", {}).get("verdict") == "hard":
        print("[FATAL] --strict：全书完整性 hard 破损(缺章/重复/乱序·数据丢失) → exit 2",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
