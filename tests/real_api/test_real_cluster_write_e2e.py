#!/usr/bin/env python3
"""真 gen-model API cluster-write 端到端（2026-06-17 · 用户定调「需要 API 的测试老老实实用 API」）。

与 fake-LLM 夹具（tests/test_cluster_write_fake_llm_e2e.py）互补：fake 验确定性骨架，
**本测试用真 gen-model 跑整条 cluster-write 写作主轨**，抓 fake 掩盖的真模型行为 bug——
reasoning 模型截断、schema 合规、gen_throttle/520 限流、真实正文长度/质量。

🔴 隔离与运行方式（见 memory feedback_real_api_tests_no_economize）：
- 放在 tests/real_api/ 子目录·**不被默认 `python tests/run_tests.py` 自动 glob**（它只扫
  tests/ 顶层 test_*.py）→ 默认快速套件不会花钱/变慢/非确定。
- 再加 env 门控 RUOYU_RUN_REAL_API=1 双保险（误跑也只 SKIP）。
- max_tokens 用 active profile 满值（gemini_pro_preview/elysiver=65536·拉满不节省）。
- GEN_MIN_INTERVAL_S 限流兜底防中转站 <15rpm 的 Cloudflare 520。

运行：
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_cluster_write_e2e.py
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent          # tests/
sys.path.insert(0, str(_TESTS))                          # import 兄弟夹具
sys.path.insert(0, str(_TESTS.parent / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import gen_model_loader as gml  # noqa: E402


@contextmanager
def _force_profile(name):
    """测试内强制 active/callable profile = 指定名（不改全局 .env·尊重用户锁定配置）。

    用于活跃 profile 端点临时故障时（如 elysiver 500/挂死）切到已知可用端点跑真 API 测试。
    patch GenModelLoader 类方法 → gen_writer in-process 构造的 loader 实例全生效。"""
    if not name:
        yield
        return
    prof = gml.GenModelLoader().get_profile(name)
    saved_a = gml.GenModelLoader.get_active_profile
    saved_c = gml.GenModelLoader.get_callable_profiles
    gml.GenModelLoader.get_active_profile = lambda self: prof
    gml.GenModelLoader.get_callable_profiles = lambda self: [prof]
    try:
        yield prof
    finally:
        gml.GenModelLoader.get_active_profile = saved_a
        gml.GenModelLoader.get_callable_profiles = saved_c

# 复用 P0 夹具（沙盒/种子/UTF-8/环境隔离/in-process runner）·但**不引入** fake seam / 网络兜底
from test_cluster_write_fake_llm_e2e import (  # noqa: E402
    _ROOT, _Sandbox, _seed_min_subsystems, _utf8_io, _frozen_environ,
    _inprocess_runner,
)

GATE = os.environ.get("RUOYU_RUN_REAL_API")


def _cjk(s: str) -> int:
    return sum(1 for ch in s if "一" <= ch <= "鿿")


def test_real_cluster_write_full_pipeline():
    """真 gen-model 跑 cluster-write 7 步：真 writer 产正文 + 真 judge + 真 splitter。
    断言管线跑通 + 真模型产出健康长度正文（不断言风格质量·那是金标准 eval 线）。"""
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过真 API 测试（默认不花钱）")
        return

    # 活跃端点临时故障时可经 RUOYU_REAL_API_PROFILE 切已知可用 profile（不改全局配置）
    force = os.environ.get("RUOYU_REAL_API_PROFILE", "")
    with _Sandbox() as sb, _force_profile(force):
        _seed_min_subsystems(sb.proj)
        with _frozen_environ(), _utf8_io():
            # max_tokens 拉满 = 用 active profile 的 65536（gen_writer freestyle 默认即用 profile 满值·
            # 不传 --target-cjk/--max-tokens 即不设上限）。仅设限流兜底 + 单稿（避免冗余 best-of-N·
            # 仍全程真调用·非省 token）。
            os.environ.setdefault("GEN_MIN_INTERVAL_S", "4.5")  # <15rpm 端点防 520
            os.environ["BEST_OF_N"] = "1"
            summary = orc.run_command(
                "cluster-write", "冒烟书", key="001",
                script_runner=_inprocess_runner,   # in-process·真 .env 真 gen-model（无 monkeypatch）
                judge_dispatch=None, pause_handler=None, auto_pilot=True,
                repo_root=_ROOT)

        assert summary.end_report.get("ok"), f"真 API cluster-write 应跑通: {summary.end_report}"
        done = [o for o in summary.completed if o.status == "completed"]
        assert len(done) == 7, f"应 7 步全过·实际 {len(done)}: {[(o.n, o.status) for o in summary.completed]}"

        draft = sb.proj / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"
        assert draft.exists(), "真 gen_writer 未产出 draft.txt"
        txt = draft.read_text(encoding="utf-8")
        n = _cjk(txt)
        print(f"\n[REAL-API] 真 gen-model 产出 {n} CJK 正文 · 开头 120 字:\n{txt[:120]}\n")
        # 真模型应产健康长度正文（freestyle 健康区间 8000-30000·下限放宽到 5000 容模型波动）
        assert n > 5000, f"真模型应产健康长度正文·实际仅 {n} CJK（疑似截断/thinking 吃光预算）"

        # 真 splitter 切章
        import json
        wal = sb.proj / "_数据库" / ".wal" / "splitter_cluster_001_decisions.json"
        assert wal.exists(), "真 splitter 未产 WAL"
        wj = json.loads(wal.read_text(encoding="utf-8"))
        assert wj.get("chapters_split", 0) >= 1, f"真 splitter 应切出 ≥1 章: {wj.get('chapters_split')}"
        print(f"[REAL-API] 真 splitter 切 {wj.get('chapters_split')} 章 · chapter_range={wj.get('chapter_range')}")


if __name__ == "__main__":
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  [FAIL] {nm}: {e}")
            traceback.print_exc()
    sys.exit(1 if fails else 0)
