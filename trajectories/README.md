# Full example trajectories

Complete agent runs on two held-out targets — every reasoning turn, tool call, tool result, and the final report with rendered figures. Compare the base model (0 figures, meta-framed) against the fine-tuned model (surface-gate-first, 9–15 figures).

### PTK7
- [Fine-tuned Qwen3.5-9B (blind)](PTK7-finetuned.md) — 60 tool calls, 14 figures
- [Base Qwen3.5-9B (rubric-conditioned)](PTK7-base.md) — 44 tool calls, 0 figures

### CLDN6
- [Fine-tuned Qwen3.5-9B (blind)](CLDN6-finetuned.md) — 60 tool calls, 8 figures
- [Base Qwen3.5-9B (rubric-conditioned)](CLDN6-base.md) — 36 tool calls, 0 figures
