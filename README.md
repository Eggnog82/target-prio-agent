# Fine-tuning Open-source Models for Target Prioritization Workflows

In this project, Qwen3.5-9B was fine-tuned to do tumor-associated antigen (TAA) target prioritization, a very important step in evaluating a target for CAR-T/TCE/antibody modalities in drug discovery and a workflow filled with expert knowledge (see examples below). Open-source models are getting better and pharma companies want their own proprietary models for drug development workflows (maybe Claude is too expensive or it cannot be used, like in China). This TAA target-prio workflow is long-horizon and uses tool calls from ~20 public/internal sources to produce the final evaluation for a target. 

The fine-tuned 9B scores **4.01 / 5** on 10 held-out target queries, compared to **1.08 / 5** on Qwen3.5-9B base model. The fine-tuned 9B also beats the base model when it was conditioned on the rubric (2.7 / 5), and approaches the frontier teacher (4.6 / 5). 

You can try the agent here: [models.frontwind.ai/agent].

---

## System overview

```mermaid
flowchart LR
    A[27-tool biology<br/>RL environment] --> B[Frontier teacher<br/>runs as an agent]
    B --> C[LLM judge scores<br/>24-dim expert rubric]
    C -->|accept high-scoring| D[SFT dataset<br/>rejection-sampled]
    C -->|reject| X[discard]
    D --> E[SFT distillation<br/>Qwen3.5-9B, full FT]
    E --> F[Agentic rubric eval<br/>blind, held-out]
    F --> G[Deployed streaming agent<br/>GPU serving]
```

---

## 1. The environment (verifiable, single-source tools)

The agent operates in a live, read-only biology environment of ~27 tools, connecting to databases such as UniProt, TCGA, GTEx, Human Protein Atlas, CPTAC/PDC proteomics, DepMap, Open Targets, cBioPortal, PaxDb, PubMed, ClinicalTrials/AACT, and more. The agent is also given a `code_exec` tool which allows it to run code in a sandbox and save files (i.e. graphs). The agent is given the ability to read and write files in a sandbox directory. Some tools may return CSVs, like per-sample gene expression from TCGA.

## 2. The reward: a 24-dimension expert rubric

Quality of a trajectory is defined by a 24-dimension rubric co-designed with an industry expert (20 years experience in big pharma, Director of R&D) spanning the real decision criteria for a surface target: surface accessibility and topology, tumor-vs-normal differential expression, normal-tissue safety, shedding, isoform structure, molecular-subtype dependence, connection to cancer drivers, competitive/clinical landscape. Many dimensions require a specific figure with an exact spec (chart type, axes, what each point is), which can be generated using the `code_exec` tool — so a missing or wrong figure is dockable. The rubric is the reward signal for both data selection and evaluation.

## 3. Data: LLM-judge rejection-sampling distillation

The SFT set is built by **rejection sampling** the teacher's trajectories:

1. **Generate** — a frontier model (Claude Sonnet 5) runs as an agent over the environment on a spread of target queries, producing full multi-tool trajectories (reasoning + tool calls + figures + final report). The frontier model is given the rubric in the system prompt and told to produce a trajectory that will score full marks. 
2. **Judge** — an LLM judge (Claude Opus 4.8) scores each trajectory on all 24 rubric dimensions.
3. **Filter** — keep only trajectories clearing >=4 avg. score and ==5 on evidence faithfulness dimensions. In one build, **142 trajectories were kept and ~277 rejected.**

## 4. Training

- **Teacher:** Claude Sonnet 5.
- **Student:** Qwen3.5-9B.
- **Objective:** SFT with assistant-token loss masking.
- **Scale:** full-parameter fine-tune via DeepSpeed ZeRO-2 with CPU optimizer offload on 2×H100.
- **Dataset:** 142 filtered trajectories from teacher model.

Note: Mid-trajectory reasoning turns from Claude Sonnet 5 were put into `<think></think>` tags in the Qwen chat template. 

## 5. Results

Models were evaluated on 10 held-out targets, judged by a frontier LLM (Claude Opus 4.8) against the 24-dimension rubric (1–5). "Blind" = no rubric in the prompt; "conditioned" = rubric injected.

| Model | Avg. rubric score | Figures produced |
|---|---|---|---|
| Base Qwen3.5-9B | 1.08 | 0–1 |
| Base Qwen3.5-9B, **rubric-conditioned** | 2.54 | 0–1 |
| **Fine-tuned Qwen3.5-9B, blind (ours)** | **4.01** | **8–12** |
| Frontier teacher (reference) | ~4.6 | ~4.6 | 8–12 |

The fine-tuned student, blind, — beats the base model that was conditioned on the rubric by ~1.5 points. The fine-tune also produces real, rendered figures (8–12 per report) where the base model produces essentially none.

### Example trajectories (base vs. fine-tuned, held-out targets)

Same environment, judged blind. The base model — *even handed the rubric* — writes meta-framed prose with **zero figures**; the fine-tuned model reasons **surface-gate-first** in an expert voice, calls the right tools, and renders **9–17 real figures**. On PTK7 the two even reach **opposite recommendations**.

**PTK7 — ADC target, NSCLC**

| | Base 9B (rubric-conditioned) | Fine-tuned 9B (blind) |
|---|---|---|
| Tool calls | 44 | 60 |
| Figures rendered | **0** | **15** |
| Opening reasoning | *"The user wants me to evaluate PTK7… I need to gather comprehensive data across multiple dimensions…"* | *"I'll systematically gather evidence across all the key dimensions for PTK7 as an NSCLC ADC target. Let me start with the foundational calls in parallel."* |
| Recommendation | *"**REJECT / DO NOT PURSUE** — PTK7 fails to meet the critical thresholds…"* | *"**Bottom line up front:** PTK7 is a real, clinically precedented ADC target in NSCLC with a genuine tumor-vs-normal window (LUAD log2FC 0.55, LUSC log2FC 0.59)…"* |

*(PTK7 is a clinically-precedented ADC target — e.g. cofetuzumab pelidotin / PF-06647020. The base model rejects it, unsupported by any figure; the fine-tuned model recommends it with a quantified expression window and 15 rendered figures.)*

**CLDN6 — CAR-T / bispecific, ovarian**

| | Base 9B (rubric-conditioned) | Fine-tuned 9B (blind) |
|---|---|---|
| Tool calls | 36 | 60 |
| Figures rendered | **0** | **9** |
| Opening reasoning | *"The user wants me to assess CLDN6… I need to systematically evaluate this target…"* | *"I'll systematically gather evidence… starting with the foundational surface-accessibility gate…"* |
| Assessment | *"CLDN6 shows EXCELLENT tumor/normal specificity…"* (prose only) | *"**Bottom line up front:** CLDN6 clears the surface-accessibility gate cleanly, shows an outstanding tumor:normal window…"* (+ 9 figures) |

> A same-query **teacher (Claude Sonnet 5)** trajectory can be added for a full three-way; the teacher is the source the fine-tune distills toward, so the fine-tuned behavior above approximates it.


## 6. Next Steps

- **Larger, multi-sample evaluation.** Current scores are on a small held-out set; expand to more targets × multiple samples per target for tighter, lower-variance estimates.
- **On-policy RL (RLVR).** The environment + rubric already form a *verifiable-reward RL environment* — move from SFT distillation to on-policy RL (e.g. GRPO / rejection-sampling RL) to push past the teacher and fix pacing. (On-policy STaR failed on the *base* model, but the SFT'd model is a much better starting policy.)
- **Fix pacing / synthesis.** The model over-gathers (60+ tool calls) and synthesizes late; add budget-aware / "stop-and-synthesize" pressure so it self-regulates instead of relying on an external cap.
- **Add trap & triage cases.** Include bad targets (e.g. TP53, GAPDH) that should be *rejected at the surface gate*, so the model learns correct early rejection, not just thorough evaluation.
- **Serving efficiency.** Quantize (int8 / 4-bit) to fit cheaper GPUs, and distill to a smaller student (e.g. 4B) for lower-cost inference.
- **Increase SFT dataset size.**
- **Specialize to adjacent workflows** (HTE validation, bispecific AND/OR target-prio) which share tools but use slightly different rubrics/procedures.

