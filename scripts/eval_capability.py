#!/usr/bin/env python
"""Capability-retention eval: GPQA-Diamond + MMLU-Pro subsample.

Checks whether TAA fine-tuning cost the model general reasoning ability.
Run once per model; compare the two result JSONs with compare_capability.py.
"""
import argparse, json, os, re, sys, time
from collections import Counter, defaultdict

import torch
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer

# The model's generation_config says 248044 (<|endoftext|>), which is NOT the
# turn boundary -- generation runs past <|im_end|> and self-generates more turns.
IM_END = "<|im_end|>"

GPQA_DS = "hendrydong/gpqa_diamond_mc"   # ungated mirror of the 198 diamond questions
MMLU_DS = "TIGER-Lab/MMLU-Pro"
LETTERS = "ABCDEFGHIJ"


def build_gpqa():
    ds = load_dataset(GPQA_DS, split="test")
    return [
        {"id": f"gpqa-{i}", "prompt": r["problem"], "gold": _boxed(r["solution"]),
         "group": r["domain"]}
        for i, r in enumerate(ds)
    ]


def build_mmlu(per_category, seed):
    ds = load_dataset(MMLU_DS, split="test")
    by_cat = defaultdict(list)
    for i, r in enumerate(ds):
        by_cat[r["category"]].append(i)

    import random
    rng = random.Random(seed)
    picked = []
    for cat in sorted(by_cat):
        idxs = sorted(by_cat[cat])
        rng.shuffle(idxs)
        picked.extend(idxs[:per_category])
    picked.sort()

    items = []
    for i in picked:
        r = ds[i]
        opts = [o for o in r["options"] if o and o.strip().upper() != "N/A"]
        if len(opts) < 2 or r["answer"] not in LETTERS[: len(opts)]:
            continue
        body = "\n".join(f"({LETTERS[j]}) {o}" for j, o in enumerate(opts))
        tail = ", ".join(f"\\boxed{{{LETTERS[j]}}}" for j in range(len(opts)))
        items.append({
            "id": f"mmlu-{r['question_id']}",
            "prompt": f"{r['question']}\n\n{body}\n\n"
                      f"Please write your final answer in the form of {tail}",
            "gold": r["answer"],
            "group": r["category"],
        })
    return items


def _boxed(text):
    m = re.findall(r"\\boxed\{\s*([A-J])\s*\}", text)
    return m[-1] if m else None


def extract(text, truncated):
    """Answer letter from a completion.

    A truncated completion has no answer -- it was cut off mid-reasoning. Do NOT
    fall back to scraping a bare "(C)" from the tail: reasoning restates the
    options constantly, so that returns a coin-flip letter and scores it as a
    real prediction. That drove GPQA below the random-chance floor on the first
    run. Truncated => None, always.
    """
    if truncated:
        return _boxed(text)          # only trust an explicit box, if one exists
    b = _boxed(text)
    if b:
        return b
    tail = text[-400:]
    m = re.findall(r"(?:answer|Answer)\s+(?:is\s+)?[:\-]?\s*\(?([A-J])\)?\b", tail)
    return m[-1] if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-category", type=int, default=50)
    ap.add_argument("--max-new-gpqa", type=int, default=16384)
    ap.add_argument("--max-new-mmlu", type=int, default=8192)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1717)
    ap.add_argument("--limit", type=int, default=0, help="debug: cap total items")
    ap.add_argument("--benchmarks", default="gpqa,mmlu")
    ap.add_argument("--no-think", action="store_true",
                    help="render an empty <think></think> so the model answers "
                         "directly; ~20x faster, and the standard fast MC protocol")
    args = ap.parse_args()

    which = {b.strip() for b in args.benchmarks.split(",")}
    items = []
    if "gpqa" in which:
        items += build_gpqa()
    if "mmlu" in which:
        items += build_mmlu(args.per_category, args.seed)
    if args.limit:
        items = items[: args.limit]
    print(f"[data] {len(items)} items "
          f"({sum(i['id'].startswith('gpqa') for i in items)} gpqa)", flush=True)

    tok = AutoTokenizer.from_pretrained(args.model)
    # Batched decoder-only generation requires LEFT padding, else short prompts
    # get right-padded and the model continues from pad tokens.
    tok.padding_side = "left"
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token

    eos_id = tok.convert_tokens_to_ids(IM_END)
    assert eos_id is not None and eos_id >= 0, "could not resolve <|im_end|>"

    model = AutoModelForCausalLM.from_pretrained(
        args.model, dtype=torch.bfloat16, device_map="cuda"
    )
    model.eval()
    print(f"[tok] stopping on {IM_END}={eos_id}; checkpoint generation_config "
          f"said {model.generation_config.eos_token_id}", flush=True)
    model.generation_config.eos_token_id = eos_id
    model.generation_config.pad_token_id = tok.pad_token_id

    def render(p):
        msgs = [{"role": "user", "content": p}]
        try:
            return tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True,
                enable_thinking=not args.no_think)
        except Exception:
            head = f"<|im_start|>user\n{p}<|im_end|>\n<|im_start|>assistant\n"
            return head + ("<think>\n\n</think>\n\n" if args.no_think else "")

    print(f"[mode] thinking={'off' if args.no_think else 'on'}", flush=True)

    torch.manual_seed(args.seed)
    results, t0 = [], time.time()
    # GPQA needs far more reasoning room than MMLU-Pro; a single shared budget
    # either truncates GPQA or wastes hours of headroom on MMLU-Pro.
    order = sorted(range(len(items)), key=lambda i: items[i]["id"].startswith("mmlu"))
    items = [items[i] for i in order]

    for s in range(0, len(items), args.batch_size):
        chunk = items[s : s + args.batch_size]
        budget = (args.max_new_mmlu if chunk[0]["id"].startswith("mmlu")
                  else args.max_new_gpqa)
        enc = tok([render(c["prompt"]) for c in chunk],
                  return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")
        with torch.no_grad():
            out = model.generate(
                **enc,
                max_new_tokens=budget,
                do_sample=False,               # greedy: deterministic, fair A/B
                eos_token_id=eos_id,
                pad_token_id=tok.pad_token_id,
            )
        gen = out[:, enc["input_ids"].shape[1]:]
        for c, g in zip(chunk, gen):
            text = tok.decode(g, skip_special_tokens=True)
            # ran out of budget mid-reasoning rather than answering wrong
            trunc = bool(eos_id not in g.tolist())
            pred = extract(text, trunc)
            results.append({**c, "pred": pred, "correct": pred == c["gold"],
                            "n_gen_tok": int((g != tok.pad_token_id).sum()),
                            "truncated": trunc, "budget": budget,
                            "completion": text[-1500:]})
        done = len(results)
        el = time.time() - t0
        print(f"[gen] {done}/{len(items)}  {el/60:.1f}m elapsed  "
              f"eta {el/done*(len(items)-done)/60:.1f}m", flush=True)

    def score(pred):
        sub = [r for r in results if pred(r)]
        n = len(sub)
        k = sum(r["correct"] for r in sub)
        fin = [r for r in sub if not r["truncated"]]
        return {"n": n, "correct": k, "acc": (k / n if n else None),
                "unparsed": sum(r["pred"] is None for r in sub),
                "truncated": sum(r["truncated"] for r in sub),
                # accuracy among completions that actually finished; if the two
                # models truncate at different rates, this is the fair number
                "acc_finished": (sum(r["correct"] for r in fin) / len(fin)
                                 if fin else None),
                "n_finished": len(fin)}

    summary = {
        "label": args.label, "model": args.model,
        "gpqa_diamond": score(lambda r: r["id"].startswith("gpqa")),
        "mmlu_pro": score(lambda r: r["id"].startswith("mmlu")),
        "mmlu_pro_by_category": {
            g: score(lambda r, g=g: r["id"].startswith("mmlu") and r["group"] == g)
            for g in sorted({r["group"] for r in results if r["id"].startswith("mmlu")})
        },
        "config": vars(args),
        "median_gen_tok": sorted(r["n_gen_tok"] for r in results)[len(results) // 2],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    print(json.dumps({k: v for k, v in summary.items()
                      if k != "mmlu_pro_by_category"}, indent=2), flush=True)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=1)
    print(f"[done] wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
