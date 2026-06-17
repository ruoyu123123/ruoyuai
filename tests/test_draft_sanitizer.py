"""draft_sanitizer 回归测试 — 钉死 gen-model 草稿确定性清洗的纯文本逻辑。

四个纯函数 + sanitize 编排器，全部确定性、零 LLM、零网络：
- fix_pair_balance：合并被换行劈开的成对符号（引号/【】/《》/（）/‘’）
- dedup_blocks：剿整块逐字复制（freestyle expand 事故）
- split_system_panels：纯连续系统面板段按框换行
- sanitize：① 去重 ② 修腰斩 ③ 拆面板，返回 (新文本, 报告 dict)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import draft_sanitizer as ds  # noqa: E402


def test_imbalance_counts_unclosed_left_symbols():
    """_imbalance：左符号多于右 → 正欠缺数；配平或右多 → 0。"""
    assert ds._imbalance("他喊“救命") == 1          # 一个未闭合左引号
    assert ds._imbalance("他喊“救命”") == 0          # 配平
    assert ds._imbalance("【A【B") == 2              # 两个未闭合左方括号
    assert ds._imbalance("”收尾") == 0               # 右多于左不算欠缺（clamp 到 0）
    assert ds._imbalance("正文无符号") == 0
    # 多种成对符号叠加计数
    assert ds._imbalance("“【《") == 3


def test_fix_pair_balance_merges_split_quote():
    """成对引号被句末标点 + 换行劈开 → 合并回单段。"""
    text = "他大喊：“快抢救！\n\n”医生冲了进来。"
    new, merged = ds.fix_pair_balance(text)
    assert merged == 1
    assert "“快抢救！”医生冲了进来。" in new
    # 合并后不再有未闭合左符号
    assert ds._imbalance(new) == 0
    # 段落数减少 1
    assert len(new.split("\n\n")) == len(text.split("\n\n")) - 1


def test_fix_pair_balance_merges_split_bracket():
    """【】系统面板被劈开（质检长期漏查的形态）→ 合并。"""
    text = "屏幕弹出：【说明：你已实体化身。\n\n】他愣住了。"
    new, merged = ds.fix_pair_balance(text)
    assert merged == 1
    assert "【说明：你已实体化身。】他愣住了。" in new


def test_fix_pair_balance_noop_when_balanced():
    """已配平的文本 → 零合并、原样返回。"""
    text = "第一段“完整”。\n\n第二段【完整】。\n\n第三段正文。"
    new, merged = ds.fix_pair_balance(text)
    assert merged == 0
    assert new == text


def test_fix_pair_balance_guard_no_infinite_loop():
    """异常文本（永远无法配平的孤立左符号）→ guard 上限 20 段，不死循环。"""
    # 30 段，第一段有未闭合左引号，后续全是无右符号的段
    paras = ['“开头'] + [f'第{i}段正文' for i in range(30)]
    text = "\n\n".join(paras)
    new, merged = ds.fix_pair_balance(text)
    # 单段最多并 20 段（guard < 20），不会卡死，函数正常返回
    assert merged <= 20
    assert isinstance(new, str)


def test_dedup_blocks_removes_verbatim_copy():
    """连续 ≥8 段整块逐字复制（freestyle 事故）→ 删除第二份。"""
    block = [f"段落内容编号{i}" for i in range(8)]
    text = "\n\n".join(block + block)  # 复制一遍
    new, removed = ds.dedup_blocks(text)
    assert removed == 8
    # 只剩一份
    assert new == "\n\n".join(block)


def test_dedup_blocks_preserves_short_repeats():
    """零星重复（< min_run=8）→ 保护不删（如系统面板提示）。"""
    block = [f"短段{i}" for i in range(3)]
    text = "\n\n".join(block + block)  # 仅 3 段重复
    new, removed = ds.dedup_blocks(text)
    assert removed == 0
    assert new == text


def test_dedup_blocks_custom_min_run_threshold():
    """自定义 min_run 阈值 → 恰好命中边界即删。"""
    block = [f"行{i}" for i in range(4)]
    text = "\n\n".join(block + block)
    # min_run=4 时 4 段重复达标 → 删
    new, removed = ds.dedup_blocks(text, min_run=4)
    assert removed == 4
    assert new == "\n\n".join(block)


def test_split_system_panels_splits_pure_panel_run():
    """纯连续面板段 `【A】【B】【C】` → 一框一段。"""
    text = "正文。\n\n【任务接收】【积分+100】【等级提升】\n\n后续正文。"
    new, splits = ds.split_system_panels(text)
    assert splits == 2  # 3 框 → 多出 2 段
    paras = new.split("\n\n")
    assert "【任务接收】" in paras
    assert "【积分+100】" in paras
    assert "【等级提升】" in paras


def test_split_system_panels_leaves_inline_panel():
    """正文里嵌的【】（有正文残留）→ 不动，避免劈断句子。"""
    text = "屏幕弹出那句：【欢迎接手】【请签收】这让他一愣。"
    new, splits = ds.split_system_panels(text)
    # 去掉所有【...】后仍有正文残留 → 整段保持不动
    assert splits == 0
    assert new == text


def test_split_system_panels_single_panel_untouched():
    """单个面板段（< 2 框）→ 不拆。"""
    text = "【唯一一条系统提示】"
    new, splits = ds.split_system_panels(text)
    assert splits == 0
    assert new == text


def test_sanitize_pipeline_report_and_idempotent():
    """sanitize 三步联跑：报告字段齐全 + 幂等（二次跑无改动）。"""
    block = [f"重复块{i}" for i in range(8)]
    text = (
        "\n\n".join(block + block)                       # 触发 dedup
        + "\n\n他喊：“救命！\n\n”有人听见了。"            # 触发 pair_balance
        + "\n\n【A】【B】"                                 # 触发 panel split
    )
    new, rep = ds.sanitize(text)
    # 报告三键齐全且语义正确
    assert rep["dedup_segments_removed"] == 8
    assert rep["pair_merges"] == 1
    assert rep["panel_splits"] == 1
    # 幂等：清洗后再跑一次应全 0、文本不变
    new2, rep2 = ds.sanitize(new)
    assert new2 == new
    assert rep2 == {"dedup_segments_removed": 0, "pair_merges": 0, "panel_splits": 0}


def test_sanitize_empty_and_clean_input():
    """空输入 / 已干净输入 → 不崩、零改动。"""
    new, rep = ds.sanitize("")
    assert new == ""
    assert rep == {"dedup_segments_removed": 0, "pair_merges": 0, "panel_splits": 0}

    clean = "第一段。\n\n第二段“配平”。\n\n第三段【单框】。"
    new2, rep2 = ds.sanitize(clean)
    assert new2 == clean
    assert rep2 == {"dedup_segments_removed": 0, "pair_merges": 0, "panel_splits": 0}


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[draft_sanitizer] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
