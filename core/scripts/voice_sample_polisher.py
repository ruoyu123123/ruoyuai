#!/usr/bin/env python3
"""把 Claude 角色样本草稿逐段交给 gemini 润色并汇总为 voice_samples.json。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atomic_json import atomic_write_json  # noqa: E402
import cluster_lookup  # noqa: E402
from distill_replicate import call_gen_model  # noqa: E402
from gen_model_loader import GenModelConfigError, GenModelExhaustedError, GenModelLoader  # noqa: E402


WRITER_MODE = "claude_draft_gemini_polish_v29"
MIN_LENGTH_RATIO = 0.85
MAX_LENGTH_RATIO = 1.30


def _parse_json_object(text: str, source: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} 不是合法 JSON object: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source} 必须是 JSON object")
    return data


def discover_drafts(drafts_dir: Path) -> list[tuple[Path, dict]]:
    if not drafts_dir.is_dir():
        raise ValueError(f"Claude 角色样本草稿目录不存在: {drafts_dir}")
    files = sorted(drafts_dir.glob("sample_*.txt"))
    if not files:
        raise ValueError(f"{drafts_dir} 下没有 sample_*.txt")
    drafts = []
    for path in files:
        data = _parse_json_object(path.read_text(encoding="utf-8"), str(path))
        kind = data.get("kind")
        if kind not in {"style", "anti"}:
            raise ValueError(f"{path} kind 必须是 style 或 anti")
        if not str(data.get("text", "")).strip():
            raise ValueError(f"{path} 缺 text")
        clusters = data.get("from_clusters")
        if not isinstance(clusters, list):
            raise ValueError(f"{path} from_clusters 必须是 array")
        normalized = [cluster_lookup.normalize_cluster_id(value) for value in clusters]
        if any(not value for value in normalized) or normalized != clusters:
            raise ValueError(f"{path} from_clusters 必须使用 canonical cluster_id")
        if len(set(normalized)) < 2:
            raise ValueError(f"{path} from_clusters 必须包含至少 2 个不同故事块")
        if kind == "style" and not str(data.get("dim", "")).strip():
            raise ValueError(f"{path} style 样本缺 dim")
        if kind == "anti" and not str(data.get("violates", "")).strip():
            raise ValueError(f"{path} anti 样本缺 violates")
        drafts.append((path, data))
    return drafts


from text_metrics import count_cjk as _cjk_count  # noqa: E402 \u5b57\u6570\u53e3\u5f84\u5355\u4e00\u771f\u7406\u6e90


def build_prompt(
    draft: dict, material: str, voice_dna: str, *, retry_length: bool = False,
) -> tuple[str, str]:
    system = """你是角色对白润色器。Claude 已根据真实素材和 Voice DNA 写好样本草稿。
你只润色 text，使句长、用词、标点和情绪表达贴近该角色；不得改变 kind、语义、场景用途和来源。
严格返回只含 kind/text 的 JSON object，不要 markdown，不要说明。"""
    length_instruction = ""
    if retry_length:
        length_instruction = (
            f"上次体量不合格。本次 text 的 CJK 字数必须保持在草稿的 "
            f"{MIN_LENGTH_RATIO:.2f}-{MAX_LENGTH_RATIO:.2f} 倍。"
        )
    user = json.dumps(
        {
            "task": "polish_voice_sample",
            "draft": draft,
            "material": material,
            "voice_dna": voice_dna,
            "length_instruction": length_instruction,
            "output_schema": {"kind": draft["kind"], "text": "润色后的对白"},
        },
        ensure_ascii=False,
    )
    return system, user


def polish_samples(
    *, drafts_dir: Path, material_path: Path, voice_dna_path: Path,
    character: str, output: Path,
) -> dict:
    drafts = discover_drafts(drafts_dir)
    material = material_path.read_text(encoding="utf-8")
    voice_dna = voice_dna_path.read_text(encoding="utf-8")
    loader = GenModelLoader()
    style_samples: list[dict] = []
    anti_samples: list[dict] = []
    models: list[str] = []
    profiles: list[str] = []
    for index, (path, draft) in enumerate(drafts):
        draft_text = str(draft["text"])
        draft_cjk = _cjk_count(draft_text)
        if draft_cjk == 0:
            raise ValueError(f"{path.name} text 不含 CJK")
        polished_text = ""
        profile = None
        length_ratio = 0.0
        for attempt in range(2):
            system, user = build_prompt(
                draft, material, voice_dna, retry_length=attempt == 1,
            )
            reply, profile, _ = call_gen_model(
                loader, system, user, default_max_tokens=1200,
                tag=f"voice_sample_{index + 1}_try{attempt + 1}",
            )
            polished = _parse_json_object(reply, f"gemini response for {path.name}")
            if set(polished) != {"kind", "text"}:
                raise ValueError(f"{path.name} 润色结果只能含 kind/text")
            if polished.get("kind") != draft["kind"] or not str(polished.get("text", "")).strip():
                raise ValueError(f"{path.name} 润色结果改变 kind 或缺 text")
            polished_text = str(polished["text"]).strip()
            length_ratio = _cjk_count(polished_text) / draft_cjk
            if MIN_LENGTH_RATIO <= length_ratio <= MAX_LENGTH_RATIO:
                break
        else:
            raise ValueError(f"{path.name} 润色体量失衡: ratio={length_ratio:.2f}")
        item = {
            "text": polished_text,
            "from_clusters": draft["from_clusters"],
        }
        if draft["kind"] == "style":
            item["dim"] = draft["dim"]
            style_samples.append(item)
        else:
            item["violates"] = draft["violates"]
            anti_samples.append(item)
        assert profile is not None
        models.append(profile.model)
        profiles.append(profile.name)
    if not style_samples or not anti_samples:
        raise ValueError("Claude 草稿必须同时包含 style 与 anti 样本")
    result = {
        "version": 1,
        "character": character,
        "style_samples": style_samples,
        "anti_samples": anti_samples,
        "banned_phrases_candidates": [],
        "_meta": {
            "writer_mode": WRITER_MODE,
            "claude_drafts_dir": str(drafts_dir.resolve()),
            "generated_by_model": sorted(set(models)),
            "generated_by_profile": sorted(set(profiles)),
        },
    }
    atomic_write_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude 角色样本草稿的 gemini 分段润色器")
    parser.add_argument("--claude-drafts-dir", required=True)
    parser.add_argument("--material", required=True)
    parser.add_argument("--voice-dna", required=True)
    parser.add_argument("--character", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        polish_samples(
            drafts_dir=Path(args.claude_drafts_dir),
            material_path=Path(args.material),
            voice_dna_path=Path(args.voice_dna),
            character=args.character,
            output=Path(args.out),
        )
    except (ValueError, FileNotFoundError, ImportError,
            GenModelConfigError, GenModelExhaustedError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] voice samples: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
