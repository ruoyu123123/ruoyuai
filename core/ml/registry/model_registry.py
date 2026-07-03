# 🔴 2026-06-29 可成长NN架构 · 模型注册表
"""model_registry.py — 模型版本/指标/部署状态跟踪 + shadow A/B 配置。

业界参考: MLflow Model Registry → 简化为单 JSON（单机不需要数据库）
env 门控: RUOYU_MODEL_REGISTRY=1（默认 off）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REG_DIR = Path(__file__).resolve().parent
_REG_FILE = _REG_DIR / "registry.json"
_SHADOW_FILE = _REG_DIR / "shadow_config.json"

VALID_STATUSES = ("shadow", "active", "retired")


def enabled() -> bool:
    return os.environ.get("RUOYU_MODEL_REGISTRY") == "1"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(__file__).resolve().parents[3]))
    except Exception:
        return str(path)


def sync_runtime_models(registry_path: str | Path | None = None) -> dict:
    """把当前创作流程可用的模型登记进 registry。

    这是运行时治理入口：不训练、不切换模型，只记录 active/shadow 版本、路径、指标和 env gate。
    """
    if not enabled():
        return {"skipped": True, "reason": "RUOYU_MODEL_REGISTRY != 1"}
    root = Path(__file__).resolve().parents[3]
    mr = ModelRegistry(registry_path)
    registered: list[dict] = []

    vad_path = root / "core" / "ml" / "emotion_vad" / "checkpoints" / "va_base"
    if vad_path.exists():
        registered.append(mr.register(
            "emotion_vad", "v1", _rel(vad_path), {"mean_ccc": 0.8},
            data_size=12000, env_gate="RUOYU_NN_VAD", status="active"))

    coh_path = root / "core" / "ml" / "coherence" / "runs" / "coherence_v1"
    if coh_path.exists():
        registered.append(mr.register(
            "coherence_binary", "v1", _rel(coh_path),
            {"accuracy": 0.604, "precision": 0.8634, "recall": 0.1663,
             "f1": 0.2788, "spearman": 0.4863},
            data_size=25000, env_gate="RUOYU_NN_COHERENCE", status="shadow"))

    style_path = root / "core" / "ml" / "style_embed" / "runs" / "style_embed_char_v1" / "final"
    if style_path.exists():
        registered.append(mr.register(
            "style_embed", "v1", _rel(style_path), {},
            data_size=0, env_gate="EMBED_BACKEND", status="active"))

    registered.append(mr.register(
        "surprisal_gpt2", "uer-gpt2-chinese-cluecorpussmall",
        "hf://uer/gpt2-chinese-cluecorpussmall", {},
        data_size=0, env_gate="RUOYU_NN_SURPRISAL", status="active"))
    return {"registered": registered, "count": len(registered)}


def _load_registry() -> dict:
    if _REG_FILE.exists():
        try:
            return json.loads(_REG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"models": {}}


def _save_registry(reg: dict) -> None:
    _REG_DIR.mkdir(parents=True, exist_ok=True)
    _REG_FILE.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class ModelRegistry:
    """模型版本管理：注册/升级/对比/查询。"""

    def __init__(self, registry_path: str | Path | None = None):
        self._path = Path(registry_path) if registry_path else _REG_FILE
        self._dir = self._path.parent

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"models": {}}

    def _save(self, reg: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def register(self, model_name: str, version: str, path: str,
                 metrics: dict, data_size: int = 0,
                 env_gate: str = "", status: str = "shadow") -> dict:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        reg = self._load()
        models = reg.setdefault("models", {})
        model_entry = models.setdefault(model_name, {
            "versions": [], "active_version": None, "shadow_version": None,
        })
        for v in model_entry["versions"]:
            if v["version"] == version:
                v.update({
                    "path": path, "metrics": metrics, "data_size": data_size,
                    "env_gate": env_gate, "status": status,
                    "updated_at": _ts(),
                })
                break
        else:
            model_entry["versions"].append({
                "version": version, "path": path, "metrics": metrics,
                "data_size": data_size, "env_gate": env_gate,
                "status": status, "registered_at": _ts(),
            })

        if status == "active":
            old_active = model_entry.get("active_version")
            if old_active and old_active != version:
                for v in model_entry["versions"]:
                    if v["version"] == old_active:
                        v["status"] = "retired"
            model_entry["active_version"] = version
        elif status == "shadow":
            model_entry["shadow_version"] = version

        self._save(reg)
        return {"model": model_name, "version": version, "status": status}

    def promote(self, model_name: str, version: str) -> dict:
        reg = self._load()
        model_entry = reg.get("models", {}).get(model_name)
        if not model_entry:
            return {"error": f"model {model_name} not found"}
        target = None
        for v in model_entry["versions"]:
            if v["version"] == version:
                target = v
                break
        if not target:
            return {"error": f"version {version} not found"}

        old_active = model_entry.get("active_version")
        if old_active:
            for v in model_entry["versions"]:
                if v["version"] == old_active:
                    v["status"] = "retired"

        target["status"] = "active"
        target["promoted_at"] = _ts()
        model_entry["active_version"] = version
        if model_entry.get("shadow_version") == version:
            model_entry["shadow_version"] = None

        self._save(reg)
        return {"promoted": version, "retired": old_active}

    def get_active(self, model_name: str) -> dict | None:
        reg = self._load()
        model_entry = reg.get("models", {}).get(model_name)
        if not model_entry:
            return None
        active_ver = model_entry.get("active_version")
        if not active_ver:
            return None
        for v in model_entry["versions"]:
            if v["version"] == active_ver:
                return v
        return None

    def get_shadow(self, model_name: str) -> dict | None:
        reg = self._load()
        model_entry = reg.get("models", {}).get(model_name)
        if not model_entry:
            return None
        shadow_ver = model_entry.get("shadow_version")
        if not shadow_ver:
            return None
        for v in model_entry["versions"]:
            if v["version"] == shadow_ver:
                return v
        return None

    def compare(self, model_name: str) -> dict:
        active = self.get_active(model_name)
        shadow = self.get_shadow(model_name)
        if not active or not shadow:
            return {"error": "need both active and shadow versions"}
        am = active.get("metrics", {})
        sm = shadow.get("metrics", {})
        all_keys = set(am) | set(sm)
        deltas = {}
        for k in sorted(all_keys):
            av = am.get(k)
            sv = sm.get(k)
            if isinstance(av, (int, float)) and isinstance(sv, (int, float)):
                deltas[k] = {"active": av, "shadow": sv,
                             "delta": round(sv - av, 6)}
        return {
            "model": model_name,
            "active": active["version"],
            "shadow": shadow["version"],
            "metrics_comparison": deltas,
        }

    def set_status(self, model_name: str, version: str,
                   status: str) -> dict:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        reg = self._load()
        model_entry = reg.get("models", {}).get(model_name)
        if not model_entry:
            return {"error": f"model {model_name} not found"}
        for v in model_entry["versions"]:
            if v["version"] == version:
                if status == "active":
                    old_active = model_entry.get("active_version")
                    if old_active and old_active != version:
                        for ov in model_entry["versions"]:
                            if ov["version"] == old_active:
                                ov["status"] = "retired"
                    model_entry["active_version"] = version
                v["status"] = status
                self._save(reg)
                return {"model": model_name, "version": version,
                        "status": status}
        return {"error": f"version {version} not found"}

    def list_models(self) -> dict:
        reg = self._load()
        summary = {}
        for name, entry in reg.get("models", {}).items():
            summary[name] = {
                "active": entry.get("active_version"),
                "shadow": entry.get("shadow_version"),
                "total_versions": len(entry.get("versions", [])),
                "versions": [
                    {"version": v["version"], "status": v["status"],
                     "metrics": v.get("metrics", {})}
                    for v in entry.get("versions", [])
                ],
            }
        return summary


def main():
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="模型注册表 CLI")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出所有模型")
    rp = sub.add_parser("register", help="注册模型")
    rp.add_argument("model"); rp.add_argument("version")
    rp.add_argument("--path", default=""); rp.add_argument("--status", default="shadow")
    rp.add_argument("--metrics", default="{}")
    pp = sub.add_parser("promote", help="升级版本")
    pp.add_argument("model"); pp.add_argument("version")
    cp = sub.add_parser("compare", help="对比 active vs shadow")
    cp.add_argument("model")
    args = ap.parse_args()
    mr = ModelRegistry()

    if args.cmd == "list":
        print(json.dumps(mr.list_models(), ensure_ascii=False, indent=2))
    elif args.cmd == "register":
        r = mr.register(args.model, args.version, args.path,
                        json.loads(args.metrics), status=args.status)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "promote":
        print(json.dumps(mr.promote(args.model, args.version),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "compare":
        print(json.dumps(mr.compare(args.model), ensure_ascii=False, indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
