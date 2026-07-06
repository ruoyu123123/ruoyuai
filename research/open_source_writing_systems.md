# Open Source Writing Systems Research

Date: 2026-07-05
Role: ruoyuai creation pipeline upgrade teammate, external project/paper research

## Scope And Constraints

This note compares open-source long-form writing systems, screenplay/story pipelines, and papers that can inform ruoyuai's single creation chain.

Hard boundary for adoption:

`/write -> /outline -> /cluster-write -> /cluster-save-state -> 走向卡 -> /export`

Any imported mechanism must become a required step or required structured substep inside `/outline`, `/cluster-write`, `/cluster-save-state`, 走向卡, or `/export`. This document rejects separate commands, chapter-owned generation entries, downgraded paths, empty bypasses, and direct database write-back outside `/cluster-save-state`.

## Fetch And Verification Log

Local paths checked:

| Item | Local path | Result |
|---|---|---|
| moyin-creator | `D:/Desktop/ruoyuai/moyin-creator` | existed before this task |
| PlotPilot | `D:/Desktop/ruoyuai/PlotPilot` | `git clone` failed, codeload zip snapshot succeeded |
| AI_NovelGenerator | `D:/Desktop/ruoyuai/external_repos/AI_NovelGenerator` | codeload zip snapshot succeeded |
| LongWriter | `D:/Desktop/ruoyuai/external_repos/LongWriter` | codeload zip snapshot succeeded |
| Ex3-NovelWriter | `D:/Desktop/ruoyuai/external_repos/Ex3-NovelWriter` | codeload zip snapshot succeeded |
| Re3 | `D:/Desktop/ruoyuai/external_repos/_downloads/re3-story-generation-main.zip` | zip download exists, but no usable local repo was produced; use web/paper evidence instead |

Command evidence:

```powershell
Test-Path -LiteralPath 'D:/Desktop/ruoyuai/moyin-creator'
Test-Path -LiteralPath 'D:/Desktop/ruoyuai/PlotPilot'
git clone --depth 1 https://github.com/shenminglinyi/PlotPilot D:/Desktop/ruoyuai/PlotPilot
git clone --depth 1 https://github.com/YILING0013/AI_NovelGenerator D:/Desktop/ruoyuai/external_repos/AI_NovelGenerator
git clone --depth 1 https://github.com/THUDM/LongWriter D:/Desktop/ruoyuai/external_repos/LongWriter
Invoke-WebRequest -Uri 'https://github.com/shenminglinyi/PlotPilot' -Method Head -TimeoutSec 30
Invoke-WebRequest -Uri 'https://codeload.github.com/shenminglinyi/PlotPilot/zip/refs/heads/master' -OutFile external_repos/_downloads/PlotPilot-master.zip
```

Observed failures:

- `git clone` to GitHub repeatedly failed with `Failed to connect to github.com port 443` or `Recv failure: Connection was reset`.
- GitHub REST API calls returned `403`.
- GitHub HTML page for PlotPilot returned `200 OK`.
- codeload zip path was used after Recovery. It succeeded for PlotPilot, AI_NovelGenerator, LongWriter, and Ex3.
- Re3 local extraction did not produce a usable repository directory, so it is treated as web/paper-only evidence.

Web/source URLs used:

- moyin-creator: https://github.com/MemeCalculate/moyin-creator
- PlotPilot: https://github.com/shenminglinyi/PlotPilot
- AI_NovelGenerator: https://github.com/YILING0013/AI_NovelGenerator
- LongWriter / AgentWrite: https://github.com/THUDM/LongWriter and https://arxiv.org/abs/2408.07055
- Re3 / Recursive Reprompting and Revision: https://github.com/yangkevin2/emnlp22-re3-story-generation
- ConStory-Bench: https://github.com/Picrew/ConStory-Bench
- Ex3-NovelWriter: https://github.com/Taskii-Lei/Ex3-NovelWriter and https://arxiv.org/pdf/2408.08506

## Candidate Findings

### 1. moyin-creator

Evidence:

- Local path: `D:/Desktop/ruoyuai/moyin-creator`
- Repository URL: https://github.com/MemeCalculate/moyin-creator
- Key files read: `README.md`, `docs/WORKFLOW_GUIDE.md`, `package.json`, `src/workers/ai-worker.ts`, `src/components/panels/sclass/sclass-scenes.tsx`

Observed mechanisms:

- Script-to-asset chain: script parsing -> scene/shot/character extraction -> AI calibration -> director/S-class generation -> video output.
- Character consistency: README describes character bible and multi-layer identity anchors.
- Scene and storyboard calibration: workflow includes AI scene calibration, API storyboard calibration, and AI character calibration.
- Batch execution: worker code processes scenes in batches with concurrency and progress reporting.
- Seedance/S-class group generation: multi-shot merged narrative segments, multimodal references, prompt fusion, parameter constraints.

可直接移植:

-落点: `/cluster-write` required substep.
  Mechanism: structured scene/storyboard calibration before final draft acceptance. In ruoyuai terms, after `build_manifest` and before `novel-writer` final commit, require a `cluster_scene_calibration` artifact that normalizes scene goal, active characters, camera-like focus, emotion, and continuity anchors.
  Validation: unit test that each `scene_storyboard` item has `scene_goal`, `active_characters`, `continuity_anchors`, and `transition_out`; integration test rejects empty first-cluster storyboard.

-落点: `/export` required substep.
  Mechanism: final packaged output validation similar to S-class parameter checks. For text export, validate chapter order, no empty chapters, cluster draft CJK conservation, title presence, and no unresolved pending tail.
  Validation: export fixture with missing chapter/title/pending tail must hard fail.

需适配:

-落点: `/outline` required substep.
  Mechanism: character visual bible maps to ruoyuai character voice/state bible. Keep it textual and structured, not image-oriented.
  Adaptation: convert "identity anchors" to `appearance/voice/behavior/prohibition/current_state` fields in 34 subsystem JSONs.
  Validation: schema check plus manifest injection snapshot.

-落点: `/cluster-write` required substep.
  Mechanism: batch/concurrency progress reporting. ruoyuai already has plan_tracker/WAL/SSE-like progress patterns; adopt only the explicit per-scene progress ledger shape.
  Validation: plan step emits deterministic progress events without creating a new execution path.

暂不建议:

- Directly importing video/image workflow, Seedance prompt logic, or Electron UI. It is not part of the long-form text chain and would create product-scope drift.

### 2. PlotPilot

Evidence:

- Local path: `D:/Desktop/ruoyuai/PlotPilot`
- Repository URL: https://github.com/shenminglinyi/PlotPilot
- Key files read: `README.md`, `docs/ARCHITECTURE.md`, `application/engine/*`, `domain/evolution/*`

Observed mechanisms:

- Narrative engine kernel for long-form AI creation, with persistent memory, knowledge graph, auto pipeline, quality governance.
- Narrative state machine: story bible, chapter summary chain, event stream, storyline DAG, foreshadow registry.
- Vector retrieval: content index plus triple index.
- Runtime pipeline: macro planning -> beat sheet -> chapter loop -> context assembly -> LLM -> policy validation -> style drift detection -> aftermath pipeline -> vector index update -> tension scoring -> state persistence.
- Single writer dispatch for SQLite writes, SSE progress, checkpoint snapshots, failure circuit breaker.

可直接移植:

-落点: `/cluster-save-state` required substep.
  Mechanism: state reducer style action ledger. After archivist extracts facts, convert changes into typed actions, apply through a pure reducer, and persist `applied_action_ids`.
  Validation: replay same action ledger twice is idempotent; invalid action leaves prior state unchanged and records error.

-落点: `/cluster-save-state` required substep.
  Mechanism: event stream + foreshadow registry with open/suspended/consumed states.
  Validation: every newly introduced foreshadow has owner, planted_at_cluster, expected_payoff_scope; every payoff references an open item.

-落点: `/cluster-write` required substep.
  Mechanism: quality monitor with required severity classes: style drift/tension creates required directed revision candidates, while continuity and format errors hard fail.
  Validation: audit fixture proves continuity/format blocks save-state, style/tension creates required revision candidate and cannot bypass cluster audit.

需适配:

-落点: `/outline` required substep.
  Mechanism: storyline DAG. ruoyuai already uses volume events and cluster briefs; adapt DAG to `事件簇.json` as `storyline_edges` and `convergence_anchor`, not as a separate engine.
  Validation: schema validates no cycles and every edge references known cluster/event ids.

-落点: 走向卡 required substep.
  Mechanism: candidate next beats from state + remaining storyline graph.
  Adaptation: use PlotPilot's DAG idea to score candidate cards against unresolved hooks, character arc pressure, and volume convergence.
  Validation:走向卡 artifact must include `why_now`, `state_delta_preview`, `unresolved_hooks_touched`, and `convergence_score`.

暂不建议:

- Importing PlotPilot's daemon/API/UI runtime. ruoyuai already mandates Claude Code slash-command chain and plan templates; a daemon is rejected.
- Keeping PlotPilot's chapter write/save semantics. Translate only mechanisms to cluster level.

### 3. AI_NovelGenerator

Evidence:

- Local path: `D:/Desktop/ruoyuai/external_repos/AI_NovelGenerator`
- Repository URL: https://github.com/YILING0013/AI_NovelGenerator
- Key files read: `README.md`, `novel_generator/blueprint.py`, `novel_generator/chapter.py`, `novel_generator/finalization.py`, `ui/main_tab.py`, `ui/summary_tab.py`, `ui/character_tab.py`

Observed mechanisms:

- GUI workflow: generate settings, generate directory, generate chapter draft, finalize current chapter, and a separate consistency proofread button.
- Chunked chapter blueprint generation with resume from existing `Novel_directory.txt`.
- Recent chapter summarization and vector retrieval during chapter generation.
- Finalization updates global summary, character state, and vector store; writes summary/state atomically.
- Model routing per task: architecture, chapter outline, prompt draft, final chapter, consistency review.

可直接移植:

-落点: `/outline` required substep.
  Mechanism: chunked blueprint generation with resume. For ruoyuai, this means cluster/event pool generation in chunks when volume cluster count is high, writing a partial but schema-valid outline artifact after each chunk.
  Validation: interrupt after chunk N and rerun continues from N+1 without duplicating cluster ids.

-落点: `/cluster-save-state` required substep.
  Mechanism: atomic state file update pattern for summary/character state.
  Validation: simulated write error leaves prior JSON readable; successful run has no temp files and valid UTF-8 JSON.

需适配:

-落点: `/cluster-write` required substep.
  Mechanism: vector retrieval plus recent-summary context.
  Adaptation: ruoyuai already has manifest/context pack. Add retrieval results as a typed manifest section with provenance and token budget, not free text.
  Validation: manifest snapshot includes source ids, recency distance, and reason; near-cluster content has anti-copy policy.

-落点: `/cluster-save-state` required substep.
  Mechanism: finalization updates global summary/character state/vector store.
  Adaptation: all updates must go through existing archivist -> archive.json -> apply_archive, not direct text files.
  Validation: fixture chapter changes one character state and one world fact; only allowed DB JSON fields change.

暂不建议:

- Chapter-level GUI controls and manual chapter save model. This conflicts with ruoyuai's cluster-only rule.
- Optional consistency proofread as a separate button. Its checks should be required inside `/cluster-write` audit.

### 4. LongWriter / AgentWrite

Evidence:

- Local path: `D:/Desktop/ruoyuai/external_repos/LongWriter`
- Repository URL: https://github.com/THUDM/LongWriter
- Paper URL: https://arxiv.org/abs/2408.07055
- Key files read: `README.md`, `agentwrite/plan.py`, `agentwrite/write.py`, `evaluation/eval_length.py`, `evaluation/eval_quality.py`

Observed mechanisms:

- LongWriter targets 10,000+ word generation from long-context LLMs.
- AgentWrite constructs ultra-long outputs by first planning, then writing step by step from that plan.
- `plan.py` writes plan artifacts; `write.py` consumes plan lines sequentially, accumulates text, caches per-step responses, and resumes existing output.
- Evaluation includes length and quality dimensions through LongBench-Write and LongWrite-Ruler.

可直接移植:

-落点: `/cluster-write` required substep.
  Mechanism: plan-then-write with per-step cache/receipt. ruoyuai already writes a whole cluster; make `cluster_blueprint.scene_storyboard` the required plan and make every scene write produce a `scene_receipt`.
  Validation: cluster draft must reference all scene ids exactly once; failed scene can resume from last receipt without regenerating accepted scenes.

-落点: `/cluster-write` required audit.
  Mechanism: long-output length stress validation. Add minimum/target/maximum CJK bands at cluster level before splitter.
  Validation: under-length cluster fails before splitter; over-length cluster requires structured split budget and still rejects chapter writing paths.

需适配:

-落点: `/outline` required substep.
  Mechanism: plan granularity. AgentWrite uses line steps; ruoyuai should use scene-level or beat-level objects with ids, narrative function, constraints, and expected state delta.
  Validation: outline JSON schema enforces stable ids and expected outputs.

-落点: `/export` required substep.
  Mechanism: quality/length evaluator. Use deterministic local checks for length/order; any LLM judge must be wired as a required rubric with stable thresholds before it can affect the gate.
  Validation: export test fixtures for length mismatch and missing chapter hard fail.

暂不建议:

- Training/fine-tuning LongWriter model inside ruoyuai as a required dependency. It is expensive and unnecessary for a pipeline upgrade.
- Reusing AgentWrite multiprocessing/API scripts directly; use the contract shape, not the script.

### 5. Re3 / Recursive Reprompting And Revision

Evidence:

- Repository URL: https://github.com/yangkevin2/emnlp22-re3-story-generation
- Search target: "Generating Longer Stories With Recursive Reprompting and Revision"
- Local zip attempt: `D:/Desktop/ruoyuai/external_repos/_downloads/re3-story-generation-main.zip`; invalid archive on `Expand-Archive`, so no local source analysis.

Observed mechanisms from project/paper description:

- Recursive reprompting: generate longer narratives by recursively expanding higher-level plans into lower-level text.
- Revision stage: revise generated text for consistency and quality after initial generation.
- Useful abstraction: plan hierarchy + generate + revise loop, not a raw one-shot writer.

可直接移植:

-落点: `/cluster-write` required substep.
  Mechanism: recursive expansion within one cluster. Expand cluster brief -> scene plan -> scene prose -> unified cluster draft, with receipts at each boundary.
  Validation: each child scene states parent cluster id and expected state delta; final cluster draft preserves scene order and no scene is orphaned.

-落点: `/cluster-write` required audit.
  Mechanism: revision loop as a mandatory bounded reconciliation, not polish.
  Validation: audit findings create structured revision tasks with `finding_id`, `target_span`, `required_fix`, `receipt`; rerun proves finding closed or explicitly hard-fails.

需适配:

-落点: `/outline` required substep.
  Mechanism: recursive plan hierarchy.
  Adaptation: ruoyuai should not introduce chapter-level nodes as write units. Hierarchy should be volume -> cluster -> scene/beat only.
  Validation: schema rejects `chapter` as a planning owner for generation.

暂不建议:

- Unbounded recursive generation. It can create runaway cost and unstable structure. Depth and max scene count must be fixed in the required plan template.

### 6. ConStory-Bench

Evidence:

- Repository URL: https://github.com/Picrew/ConStory-Bench
- Search target: consistent story generation benchmark.
- Treated as benchmark/evaluation evidence rather than production pipeline source.

Observed mechanisms:

- Focuses on story consistency evaluation, useful for testing whether generated stories maintain entity, plot, and event consistency.
- More valuable as a regression/evaluation harness than as a writing engine.

可直接移植:

-落点: `/cluster-write` required audit and `/export` required hard check.
  Mechanism: consistency benchmark dimensions translated into local test categories: entity consistency, temporal consistency, event causality, location continuity, unresolved contradiction.
  Validation: create a small gold suite where each fixture contains a known contradiction and must be caught by the relevant category.

需适配:

-落点: `/cluster-save-state` required substep.
  Mechanism: consistency findings become state reconciliation entries. If a contradiction is factual, block save-state; if it is interpretive, store it as a revision candidate.
  Validation: hard contradiction prevents archive apply; interpretive note appears in review ledger but does not mutate DB.

暂不建议:

- Depending on the external benchmark runtime in production. Keep evaluation categories and local fixtures; do not add another service.

### 7. Ex3-NovelWriter

Evidence:

- Local path: `D:/Desktop/ruoyuai/external_repos/Ex3-NovelWriter`
- Repository URL: https://github.com/Taskii-Lei/Ex3-NovelWriter
- Paper URL: https://arxiv.org/pdf/2408.08506
- Key files read: `README.md`, `Extracting/*`, `Excelsior/*`, `Expanding/*`

Observed mechanisms:

- Extracting: recursively extract summaries and entity information from raw novels.
- Excelsior: build corpus for fine-tuning from extracted summaries/entity info.
- Expanding: generate new novels from premises/tags/intro, with outline output that users can edit.
- Useful model: derive style/genre/storyline structures from source novels, then expand from structured premises.

可直接移植:

-落点: `/outline` required substep.
  Mechanism: extracting summaries and entities from reference material before outline generation. ruoyuai already has style/character distillation; add reference-derived `genre_storyline_patterns` only when source material is explicitly provided.
  Validation: extraction output has source ids, entities, recurring conflicts, pacing pattern; no raw copyrighted text is copied into prompts beyond local allowed reference handling.

需适配:

-落点: `/cluster-write` required substep.
  Mechanism: genre imitation from extracted structures.
  Adaptation: use as style/structure constraints in manifest, not as fine-tuning corpus generation.
  Validation: manifest contains distilled pattern ids and anti-copy guard; audit checks similarity risk.

暂不建议:

- Fine-tuning workflow and dataset construction as part of normal ruoyuai writing. It is heavy, data-sensitive, and outside the single slash-command chain.

## Consolidated Portable Checklist

### 可直接移植

| Mechanism | Source | Required landing | Implementation shape | Verification |
|---|---|---|---|---|
| Scene/storyboard calibration artifact | moyin-creator | `/cluster-write` | required `cluster_scene_calibration` before writer finalization | schema + first-cluster storyboard nonempty test |
| Export hard validation | moyin-creator, LongWriter eval | `/export` | required export integrity checklist | missing chapter/title/pending tail fixtures fail |
| Typed state reducer action ledger | PlotPilot | `/cluster-save-state` | archivist output -> typed actions -> pure reducer -> JSON DB | idempotent replay and invalid action tests |
| Foreshadow registry lifecycle | PlotPilot | `/cluster-save-state` | open/suspended/consumed with owner and payoff scope | payoff references open item test |
| Chunked outline generation with resume | AI_NovelGenerator | `/outline` | chunk cluster/event pool, write valid partial artifact | interrupt/resume no duplicate ids |
| Atomic state writes | AI_NovelGenerator | `/cluster-save-state` | temp file + replace for generated DB JSON artifacts | simulated write failure keeps prior JSON readable |
| Plan-then-write scene receipts | LongWriter AgentWrite | `/cluster-write` | scene plan -> scene receipt -> cluster draft assembly | all scene ids exactly once; resume from receipt |
| Bounded recursive expansion + revision tasks | Re3 | `/cluster-write` | cluster -> scene/beat -> draft -> required revision receipts | finding closure or hard fail |
| Consistency regression categories | ConStory-Bench | `/cluster-write`, `/export` | local contradiction gold fixtures | each category catches fixture |

### 需适配

| Mechanism | Source | Required landing | Adaptation needed | Verification |
|---|---|---|---|---|
| Storyline DAG | PlotPilot | `/outline`, 走向卡 | map to `事件簇.json.storyline_edges`, no separate engine | acyclic edge schema |
| Candidate next beat scoring | PlotPilot | 走向卡 | add `why_now`, `state_delta_preview`, hook touch list, convergence score |走向卡 artifact required fields |
| Vector retrieval provenance | AI_NovelGenerator, PlotPilot | `/cluster-write` | typed manifest section with source ids and token budget | manifest snapshot + anti-copy policy |
| Task-specific model routing | AI_NovelGenerator | `/outline`, `/cluster-write`, `/cluster-save-state` | map to existing gen-model config, no UI switch path | plan template declares model role |
| Long-output length/quality evaluation | LongWriter | `/cluster-write`, `/export` | deterministic CJK bands first; LLM judge only if thresholded | under/over length fixtures |
| Reference pattern extraction | Ex3 | `/outline` | source-derived structural patterns only, no fine-tune path | extraction output provenance and similarity audit |

### 暂不建议

| Mechanism | Reason |
|---|---|
| PlotPilot daemon/API/UI runtime | Creates another runtime beside ruoyuai slash-command chain |
| PlotPilot chapter write/save semantics | Conflicts with cluster-only rule |
| AI_NovelGenerator GUI chapter controls | Chapter is output format in ruoyuai, not writing unit |
| AI_NovelGenerator separate consistency button | Must be required audit inside `/cluster-write` |
| LongWriter model training/fine-tuning | Heavy dependency; not needed for pipeline contract upgrade |
| AgentWrite multiprocessing scripts | Useful contract, but scripts are not integrated with ruoyuai WAL/plan_tracker |
| Re3 unbounded recursion | Needs fixed depth and max scene count |
| ConStory-Bench runtime dependency in production | Keep local categories/fixtures instead |
| moyin video/image/Seedance workflow | Different product surface; text pipeline should only borrow structured calibration/validation |
| Ex3 corpus fine-tuning workflow | Data-sensitive and outside current main chain |

## Recommended Required-Step Upgrades

Priority 1:

1. `/cluster-write`: add required `scene_receipts` and `cluster_scene_calibration` artifacts.
2. `/cluster-save-state`: add typed state action ledger and reducer idempotency check.
3. `/export`: add hard export integrity gate.

Priority 2:

1. `/outline`: add chunked outline/event-pool generation with resume for large projects.
2. 走向卡: add candidate scoring fields: `why_now`, `state_delta_preview`, `unresolved_hooks_touched`, `convergence_score`.
3. `/cluster-write`: add contradiction gold-suite categories inspired by ConStory-Bench.

Priority 3:

1. `/outline`: conditional required substep when reference material exists: Ex3-style entity/pacing/pattern extraction.
2. `/cluster-write`: bounded recursive expansion for unusually large clusters.

## Residual Risks

- GitHub transport was unstable in this environment. `git clone` failed, but codeload snapshots succeeded for several repos. Re3 local archive was invalid, so Re3 conclusions are based on web/paper evidence.
- PlotPilot and AI_NovelGenerator are chapter-oriented in places. Any adoption must translate concepts to cluster-level contracts.
- ConStory-Bench needs a local fixture translation before it can become a ruoyuai regression lock.
- Some source projects may have changed after this snapshot. Re-run source verification before implementation work.

## PUA Report Status

One `[PUA-REPORT]` occurred during fetch because `git clone`, GitHub API, and initial codeload attempts failed repeatedly. Recovery path succeeded by switching from clone-blocking to codeload snapshots plus web/paper evidence.
