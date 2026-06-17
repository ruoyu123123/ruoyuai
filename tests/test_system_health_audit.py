"""system_health_audit 纯逻辑回归测试（零依赖·确定性·不跑 LLM/不联网）。

覆盖 4 个确定性纯函数：
- check_script_importable: 编译源码 → 检出 SyntaxError，干净文件返回 True
- check_agent_frontmatter: 解析 agent .md frontmatter（name/description 必含）
- check_command_frontmatter: 解析 command .md frontmatter（description 必含）
- check_plan_scripts_exist: 解析 plan.json + 正则抽 core/scripts/*.py 路径 → 检存在性

check_script_help 跑 subprocess（依赖真实解释器·非确定性），不在确定性测试范围。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import system_health_audit as mod  # noqa: E402


# ---------- check_script_importable ----------

def test_importable_clean_source():
    """干净的合法 Python 文件 → (True, "")。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "clean.py"
        p.write_text("x = 1\ndef foo():\n    return x + 1\n", encoding="utf-8")
        ok, msg = mod.check_script_importable(p)
        assert ok is True
        assert msg == ""


def test_importable_syntax_error():
    """含 SyntaxError 的文件 → (False, 带行号与原因的消息)。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "broken.py"
        # 第 2 行括号不闭合 → SyntaxError
        p.write_text("x = 1\ndef foo(:\n    return\n", encoding="utf-8")
        ok, msg = mod.check_script_importable(p)
        assert ok is False
        assert msg.startswith("SyntaxError L")
        # 行号必须被报出来（锁住 e.lineno 透传行为）
        assert "L2" in msg


def test_importable_empty_file_is_ok():
    """空文件可被 compile（边界）→ (True, "")。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "empty.py"
        p.write_text("", encoding="utf-8")
        ok, msg = mod.check_script_importable(p)
        assert ok is True
        assert msg == ""


# ---------- check_agent_frontmatter ----------

def test_agent_frontmatter_ok():
    """合法 frontmatter（name + description）→ (True, "ok")。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "agent.md"
        p.write_text("---\nname: foo\ndescription: bar\n---\n# body\n", encoding="utf-8")
        ok, msg = mod.check_agent_frontmatter(p)
        assert ok is True
        assert msg == "ok"


def test_agent_frontmatter_no_leading_marker():
    """不以 --- 开头 → 无 frontmatter。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "agent.md"
        p.write_text("# just a body\nname: foo\ndescription: bar\n", encoding="utf-8")
        ok, msg = mod.check_agent_frontmatter(p)
        assert ok is False
        assert "frontmatter" in msg


def test_agent_frontmatter_unclosed():
    """开头有 --- 但无第二个闭合 --- → frontmatter 未闭合。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "agent.md"
        p.write_text("---\nname: foo\ndescription: bar\n", encoding="utf-8")
        ok, msg = mod.check_agent_frontmatter(p)
        assert ok is False
        assert "未闭合" in msg


def test_agent_frontmatter_missing_fields():
    """缺 name / 缺 description 分别命中各自分支。"""
    with tempfile.TemporaryDirectory() as d:
        # 缺 name
        p1 = Path(d) / "no_name.md"
        p1.write_text("---\ndescription: bar\n---\nbody\n", encoding="utf-8")
        ok1, msg1 = mod.check_agent_frontmatter(p1)
        assert ok1 is False
        assert "name" in msg1

        # 缺 description
        p2 = Path(d) / "no_desc.md"
        p2.write_text("---\nname: foo\n---\nbody\n", encoding="utf-8")
        ok2, msg2 = mod.check_agent_frontmatter(p2)
        assert ok2 is False
        assert "description" in msg2


# ---------- check_command_frontmatter ----------

def test_command_frontmatter_ok_and_missing_desc():
    """command 只要求 description；有=ok，缺=报错。"""
    with tempfile.TemporaryDirectory() as d:
        ok_p = Path(d) / "ok.md"
        ok_p.write_text("---\ndescription: do something\n---\nbody\n", encoding="utf-8")
        ok, msg = mod.check_command_frontmatter(ok_p)
        assert ok is True
        assert msg == "ok"

        bad_p = Path(d) / "bad.md"
        bad_p.write_text("---\nname: only_name\n---\nbody\n", encoding="utf-8")
        bad_ok, bad_msg = mod.check_command_frontmatter(bad_p)
        assert bad_ok is False
        assert "description" in bad_msg


# ---------- check_plan_scripts_exist ----------

def _write_plan(plan_path: Path, steps: list) -> None:
    plan_path.write_text(json.dumps({"steps": steps}, ensure_ascii=False), encoding="utf-8")


def test_plan_scripts_missing_detected():
    """plan 引用一个不存在的 core/scripts 脚本 → 报 PLAN_SCRIPT_MISSING。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = root / "demo.plan.json"
        _write_plan(plan, [
            {"n": 1, "scripts": ["python core/scripts/does_not_exist.py --foo"]},
        ])
        findings = mod.check_plan_scripts_exist(plan, root)
        assert len(findings) == 1
        f = findings[0]
        assert f["code"] == "PLAN_SCRIPT_MISSING"
        assert f["plan"] == "demo.plan.json"
        assert f["step"] == 1
        assert f["missing_script"] == "core/scripts/does_not_exist.py"


def test_plan_scripts_present_no_finding():
    """脚本真实存在 → 零 finding（happy path）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "core" / "scripts").mkdir(parents=True)
        (root / "core" / "scripts" / "real.py").write_text("# real\n", encoding="utf-8")
        plan = root / "ok.plan.json"
        _write_plan(plan, [
            {"n": 3, "scripts": ["python core/scripts/real.py --x 1"]},
        ])
        findings = mod.check_plan_scripts_exist(plan, root)
        assert findings == []


def test_plan_scripts_non_python_cmd_ignored():
    """非 'python core/scripts/*.py' 形态的命令不被解析（无误报）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = root / "mixed.plan.json"
        _write_plan(plan, [
            {"n": 1, "scripts": ["echo hello", "git status", "node foo.js"]},
            {"n": 2, "scripts": []},          # 空 scripts
            {"n": 3},                          # 无 scripts 键
        ])
        findings = mod.check_plan_scripts_exist(plan, root)
        assert findings == []


def test_plan_no_steps_key():
    """plan 缺 steps 键 → 不崩，返回空（边界）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        plan = root / "empty.plan.json"
        plan.write_text(json.dumps({"meta": "no steps"}), encoding="utf-8")
        findings = mod.check_plan_scripts_exist(plan, root)
        assert findings == []
