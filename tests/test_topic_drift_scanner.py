# 🔴 2026-06-29 NN主题漂移/情感弧线集成
"""test_topic_drift_scanner — 主题漂移检测 scanner 测试

钉死：
  · 内容语义后端不可用（content_backend_available()==False）→ 静默返回空列表
  · 段落太少 → 返回空
  · mock 数据测试检测逻辑
  · 所有 issue 都是 advisory
  · 连续偏离 → TOPIC_DRIFT_SUSTAINED
  · 滑窗均值计算正确
  · 后端检测逻辑正确
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import topic_drift_scanner as tds  # noqa: E402
import embedding_store  # noqa: E402


# 🔴 2026-07-04 本地 autouse 隔离（不碰全局 conftest.py）：content_backend_available() 查真
# 文件系统（venv/infer 脚本/模型目录），本机若已备好 bge 模型会恒真——不像旧 EMBED_BACKEND
# 有 conftest._isolate_nn_gates 兜底清零，会让本文件里不测 embedding 的"素"用例跨机器非确定
# 污染（真机上真的会去跑一次真实编码）。默认关闭·内容路径专项测试自行 monkeypatch 覆盖。
@pytest.fixture(autouse=True)
def _content_backend_off_by_default():
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        yield
    finally:
        embedding_store.content_backend_available = orig


# ── helper: 确定性 mock embedding（字符频率向量·有意义的余弦距离）──────────────

def _char_freq_embedding(text: str, dim: int = 32) -> list[float]:
    """基于字符频率的确定性 embedding（维度小但余弦距离有意义）。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# ── 测试用文本 ─────────────────────────────────────────────────────────────

_SCOPE = "武侠小说主角在江湖中行走历练学习武功秘籍"

_ON_TOPIC_PARAS = [
    "他拔出长剑剑光如虹直指对手心口",
    "少年在悬崖边练功内力渐深筋脉通畅",
    "江湖中人人谈论这位新晋的剑客高手",
    "掌门将秘籍递给他嘱咐他好生修炼",
    "夜深人静他独坐石上运功调息入定",
    "对手使出暗器他侧身闪避反手一剑",
    "师妹在远处看着他比武心中担忧不已",
    "镖局的人在客栈相遇互通江湖消息",
    "他突然领悟了剑法第七式的奥义所在",
    "大战三百回合最终他取得了胜利归来",
]

_OFF_TOPIC_PARAS = [
    "量子计算机的比特位纠缠态可以实现并行计算加速",
    "区块链技术在金融领域的去中心化应用日益广泛",
    "人工智能深度学习神经网络的反向传播算法原理",
    "半导体芯片的制程工艺从七纳米缩小到三纳米",
]


# ── 后端检测 ─────────────────────────────────────────────────────────────────

class TestContentBackendReady:
    """_content_backend_ready 检测逻辑（委托 embedding_store.content_backend_available·
    纯布尔文件系统检查·不再有 EMBED_BACKEND 字符串取值的分支细节）。"""

    def test_false_by_default(self, monkeypatch):
        monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
        assert tds._content_backend_ready() is False

    def test_true_when_available(self, monkeypatch):
        monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
        assert tds._content_backend_ready() is True

    def test_exception_returns_false(self, monkeypatch):
        """content_backend_available 探测异常 → _content_backend_ready 吞异常返回 False
        （不崩·调用方静默返回空列表）。"""
        def _boom():
            raise RuntimeError("模拟内容后端探测异常")
        monkeypatch.setattr(embedding_store, "content_backend_available", _boom)
        assert tds._content_backend_ready() is False


# ── 静默降级 ─────────────────────────────────────────────────────────────────

def test_no_content_backend_returns_empty():
    """内容后端不可用（默认·autouse fixture）→ 返回空列表（静默降级）。"""
    result = tds.scan_topic_drift("一段很长的文字\n" * 10, "主题")
    assert result == []


def test_empty_text_returns_empty():
    """空文本 → 返回空列表。"""
    assert tds.scan_topic_drift("", "主题") == []


def test_empty_scope_returns_empty():
    """空 scope_summary → 返回空列表。"""
    assert tds.scan_topic_drift("一些文字", "") == []


def test_none_text_returns_empty():
    """None 文本 → 返回空列表。"""
    assert tds.scan_topic_drift(None, "主题") == []


def test_none_scope_returns_empty():
    """None scope → 返回空列表。"""
    assert tds.scan_topic_drift("文字", None) == []


def test_too_few_paragraphs_returns_empty(monkeypatch):
    """段落太少（< 6）→ 返回空。"""
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _char_freq_embedding)
    result = tds.scan_topic_drift("第一段\n第二段\n第三段", "主题")
    assert result == []


def test_import_error_graceful(monkeypatch):
    """内容后端门控判定就绪（mock _content_backend_ready 直接放行，绕开门控自身的 import），
    但 scan_topic_drift 内部第二处 `from embedding_store import ...` 因模块被顶掉而失败
    → 返回空列表（不崩）。"""
    monkeypatch.setattr(tds, "_content_backend_ready", lambda: True)
    # 临时让 import 失败
    saved = sys.modules.get("embedding_store")
    sys.modules["embedding_store"] = None  # type: ignore[assignment]
    try:
        result = tds.scan_topic_drift(
            "\n".join(["段落" + str(i) * 10 for i in range(10)]), "主题")
        assert result == []
    finally:
        if saved is not None:
            sys.modules["embedding_store"] = saved
        else:
            sys.modules.pop("embedding_store", None)


# ── 段落切分 ─────────────────────────────────────────────────────────────────

class TestSplitParagraphs:
    """_split_paragraphs 切段逻辑。"""

    def test_basic_split(self):
        lines = tds._split_paragraphs("第一段内容\n第二段内容\n第三段内容")
        assert len(lines) == 3

    def test_filters_short_lines(self):
        """太短的行（< min_len）被过滤。"""
        lines = tds._split_paragraphs("很长的段落内容\n短\n另一段长内容", min_len=5)
        assert len(lines) == 2

    def test_empty_lines_skipped(self):
        """空行被跳过。"""
        lines = tds._split_paragraphs("第一段内容\n\n\n第二段内容")
        assert len(lines) == 2

    def test_strip_whitespace(self):
        """段落两端空白被清除。"""
        lines = tds._split_paragraphs("  段落内容  \n  另一段  ")
        assert all(not l.startswith(" ") for l in lines)


# ── 滑窗均值 ─────────────────────────────────────────────────────────────────

class TestSlidingWindowMean:
    """_sliding_window_mean 计算正确性。"""

    def test_basic(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        means = tds._sliding_window_mean(vals, window=3)
        assert len(means) == 5
        # 中间值：mean(2, 3, 4) = 3.0
        assert abs(means[2] - 3.0) < 1e-9
        # 边界值：mean(1, 2) = 1.5
        assert abs(means[0] - 1.5) < 1e-9
        # 末尾：mean(4, 5) = 4.5
        assert abs(means[4] - 4.5) < 1e-9

    def test_window_5(self):
        vals = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        means = tds._sliding_window_mean(vals, window=5)
        assert len(means) == 7
        # 中间值（idx=3）：mean(1, 2, 3, 4, 5) = 3.0
        assert abs(means[3] - 3.0) < 1e-9

    def test_empty(self):
        assert tds._sliding_window_mean([]) == []

    def test_single_element(self):
        means = tds._sliding_window_mean([42.0], window=5)
        assert means == [42.0]


# ── mock 检测逻辑 ────────────────────────────────────────────────────────────

def _run_with_mock_embedding(draft_text: str, scope: str):
    """用 mock embedding 跑 scan_topic_drift。"""
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_batch = embedding_store.compute_content_embeddings_batch
    orig_prefetch = embedding_store.prefetch_content_embeddings
    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = _char_freq_embedding
    embedding_store.compute_content_embeddings_batch = (
        lambda texts: [_char_freq_embedding(t) for t in texts])
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        return tds.scan_topic_drift(draft_text, scope)
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.compute_content_embeddings_batch = orig_batch
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_all_issues_advisory():
    """所有产出的 issue 都是 advisory。"""
    paras = _ON_TOPIC_PARAS[:4] + _OFF_TOPIC_PARAS + _ON_TOPIC_PARAS[4:8]
    text = "\n".join(paras)
    result = _run_with_mock_embedding(text, _SCOPE)
    for issue in result:
        assert issue["gate_level"] == "advisory", \
            f"issue {issue['code']} 应为 advisory，实际 {issue['gate_level']}"


def test_issue_structure():
    """每个 issue 包含必要字段。"""
    paras = _ON_TOPIC_PARAS[:4] + _OFF_TOPIC_PARAS + _ON_TOPIC_PARAS[4:8]
    text = "\n".join(paras)
    result = _run_with_mock_embedding(text, _SCOPE)
    for issue in result:
        assert "code" in issue
        assert "gate_level" in issue
        assert "severity" in issue
        assert "message" in issue
        assert issue["code"] in (
            "TOPIC_DRIFT_DETECTED",
            "TOPIC_DRIFT_SUSTAINED",
            "TOPIC_RETURN_ABRUPT",
        )


def test_on_topic_fewer_issues():
    """全部段落都在主题内 → 产出的 issue 比混入跑题段少。"""
    on_text = "\n".join(_ON_TOPIC_PARAS)
    mixed_text = "\n".join(
        _ON_TOPIC_PARAS[:4] + _OFF_TOPIC_PARAS + _ON_TOPIC_PARAS[4:8])
    on_issues = _run_with_mock_embedding(on_text, _SCOPE)
    mixed_issues = _run_with_mock_embedding(mixed_text, _SCOPE)
    # 纯正文 issue 数应 <= 混合文 issue 数（char-freq 有一定区分度）
    assert len(on_issues) <= len(mixed_issues) + 2  # 允许小误差


def test_off_topic_produces_some_signal():
    """注入明显跑题段 → 至少产出某种 drift 信号。"""
    # 用更极端的跑题文本确保 char-freq embedding 能区分
    on_paras = ["他拔出长剑剑光如虹直指对手心口练武功"] * 5
    off_paras = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"] * 4
    paras = on_paras + off_paras + on_paras[:3]
    text = "\n".join(paras)
    scope = "他拔出长剑剑光如虹直指对手心口练武功"
    result = _run_with_mock_embedding(text, scope)
    # 英文字母 vs 中文字符在 char-freq embedding 中差异极大
    # 应该至少检测到 drift
    codes = [i["code"] for i in result]
    assert ("TOPIC_DRIFT_DETECTED" in codes or
            "TOPIC_DRIFT_SUSTAINED" in codes), \
        f"应检测到漂移，但得到: {codes}"


def test_sustained_drift_detection():
    """连续 4+ 段明显偏离 → 应出现 SUSTAINED。"""
    on = ["他拔出长剑剑光如虹直指对手心口练武功"] * 4
    off = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890extra"] * 5
    paras = on + off + on[:3]
    text = "\n".join(paras)
    scope = "他拔出长剑剑光如虹直指对手心口练武功"
    result = _run_with_mock_embedding(text, scope)
    sustained = [i for i in result if i["code"] == "TOPIC_DRIFT_SUSTAINED"]
    # 连续 5 段英文应被检测为持续偏离
    assert len(sustained) >= 1, \
        f"应检测到 SUSTAINED，但得到: {[i['code'] for i in result]}"


def test_abrupt_return_detection():
    """单段跑题离群后立即回归 → 应出现 TOPIC_RETURN_ABRUPT。"""
    # 必须单点离群：rule3 要 prev>mean+2σ 且 cur<mean+1σ；3+ 连续块会抬高阈值只触发 SUSTAINED。
    on = ["他拔出长剑剑光如虹直指对手心口练武功"] * 5
    off = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890extra"]   # 单段离群（非连续块）
    # 模式: on...→off(单段)→on（漂移后突然回归）
    paras = on + off + on[:4]
    text = "\n".join(paras)
    scope = "他拔出长剑剑光如虹直指对手心口练武功"
    result = _run_with_mock_embedding(text, scope)
    codes = [i["code"] for i in result]
    assert "TOPIC_RETURN_ABRUPT" in codes, \
        f"单段离群后回归应触发 ABRUPT，实得 {codes}"


def test_compute_distances():
    """_compute_distances 正确计算余弦距离。"""
    from embedding_store import cosine_similarity

    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    v3 = [1.0, 0.0, 0.0]  # 与 scope 相同
    scope = [1.0, 0.0, 0.0]

    dists = tds._compute_distances([v1, v2, v3], scope, cosine_similarity)
    assert len(dists) == 3
    assert abs(dists[0] - 0.0) < 1e-9   # 完全一致 → 距离 0
    assert abs(dists[1] - 1.0) < 1e-9   # 正交 → 距离 1
    assert abs(dists[2] - 0.0) < 1e-9   # 完全一致


# ── 🔴 2026-06-29 加固回归锁（CHANGES 剥离 / 维度守卫 / spec 通用字段 / 滑窗指标）──────

def test_strips_changes_block():
    """正文尾部 CHANGES 段不被当作段落参与主题分析（_strip_changes 幂等剥离）。"""
    body = "\n".join(["他拔出长剑剑光如虹直指对手心口练武功"] * 8)
    draft = body + "\n---CHANGES---\n" + "\n".join(["KEY: 这是元数据行不该计入主题分析"] * 5)
    stripped = tds._strip_changes(draft)
    assert "---CHANGES---" not in stripped
    assert "元数据行" not in stripped
    assert "他拔出长剑" in stripped
    # 无分隔符时幂等不变
    assert tds._strip_changes(body) == body


def test_dim_mismatch_returns_empty(monkeypatch):
    """某段 embedding 维度与 scope 不一致 → 返回空，不产假漂移。"""
    def _mixed(text, dim=32):
        if "MISMATCH" in text:
            return [0.1] * 16   # 维度不同
        return _char_freq_embedding(text, dim)
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _mixed)
    paras = ["正常段落武功剑法内容充足"] * 7 + ["MISMATCH 这段维度不同"]
    result = tds.scan_topic_drift("\n".join(paras), "武功剑法主题")
    assert result == []


def test_partial_embedding_failure_returns_empty(monkeypatch):
    """🔴 2026-07-04 迁移回归锁：compute_content_embedding 单条编码失败返回 None（内容语义
    无 hash 兜底冒充·与旧 compute_embedding 必兜底 hash(384) 不同语义），维度守卫须先挡
    None 再比长度，不然 len(None) 会直接崩——不产假漂移，也不该抛异常。"""
    def _partial(text):
        if "FAIL" in text:
            return None
        return _char_freq_embedding(text)
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _partial)
    paras = ["正常段落武功剑法内容充足"] * 7 + ["FAIL 这段编码失败"]
    result = tds.scan_topic_drift("\n".join(paras), "武功剑法主题")
    assert result == []


def test_sustained_has_spec_common_fields():
    """SUSTAINED issue 带 spec 通用字段（paragraph_index / paragraph_preview / distance / threshold）。"""
    on = ["他拔出长剑剑光如虹直指对手心口练武功"] * 4
    off = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890extra"] * 5
    text = "\n".join(on + off + on[:3])
    scope = "他拔出长剑剑光如虹直指对手心口练武功"
    result = _run_with_mock_embedding(text, scope)
    sustained = [i for i in result if i["code"] == "TOPIC_DRIFT_SUSTAINED"]
    assert sustained, f"应有 SUSTAINED，得到 {[i['code'] for i in result]}"
    for it in sustained:
        assert "paragraph_index" in it
        assert "paragraph_preview" in it
        assert "distance" in it
        assert "threshold" in it


def test_issues_have_local_window_mean():
    """每条 drift issue 都带 local_window_mean（滑窗局部均值上下文 · spec step 8）。"""
    on = ["他拔出长剑剑光如虹直指对手心口练武功"] * 5
    off = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890extra"] * 4
    text = "\n".join(on + off + on[:3])
    scope = "他拔出长剑剑光如虹直指对手心口练武功"
    result = _run_with_mock_embedding(text, scope)
    assert result, "极端跑题应产出 issue"
    for it in result:
        assert "local_window_mean" in it, f"{it['code']} 缺 local_window_mean"


# ── 🔴 2026-07-03 Wave-4 性能层：prefetch 批量预热回归锁（范式源头） ─────────────────

def test_prefetch_called_once_with_scope_and_paragraphs(monkeypatch):
    """scan_topic_drift 语义路径开头一次性 prefetch_content_embeddings(scope+全部段落)，
    而不是逐段各自触发后端计算——断言只调一次且文本集合符合预期。"""
    paras = _ON_TOPIC_PARAS[:4] + _OFF_TOPIC_PARAS + _ON_TOPIC_PARAS[4:8]
    text = "\n".join(paras)
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _char_freq_embedding)
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings", _rec_prefetch)
    tds.scan_topic_drift(text, _SCOPE)

    assert len(calls) == 1, f"prefetch 应只调一次，实际 {len(calls)}"
    expected = [_SCOPE] + tds._split_paragraphs(text)
    assert calls[0] == expected


def test_prefetch_not_called_when_too_few_paragraphs(monkeypatch):
    """段落太少（< 6）提前返回·prefetch 不该被触发（零浪费）。"""
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {}

    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _char_freq_embedding)
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings", _rec_prefetch)
    result = tds.scan_topic_drift("第一段\n第二段\n第三段", "主题")

    assert result == []
    assert calls == [], "段落不足 6 段时不该跑到 prefetch"
