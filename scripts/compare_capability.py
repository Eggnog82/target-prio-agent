#!/usr/bin/env python
"""Compare two capability-eval runs on the same items.

Same questions scored by both models, so the paired test (McNemar) is the right
one -- an unpaired proportion test throws away the pairing and is less sensitive.
"""
import argparse, json, math
from collections import Counter


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar(b, c):
    """Exact two-sided binomial test on discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def load(path):
    with open(path) as f:
        d = json.load(f)
    return d["summary"], {r["id"]: r for r in d["results"]}


def section(name, base, ft, base_r, ft_r, finished_only=False):
    ids = sorted(set(base_r) & set(ft_r))
    if finished_only:
        ids = [i for i in ids
               if not base_r[i]["truncated"] and not ft_r[i]["truncated"]]
        name += " (both finished)"
    if not ids:
        return
    bk = sum(base_r[i]["correct"] for i in ids)
    fk = sum(ft_r[i]["correct"] for i in ids)
    n = len(ids)
    # discordant pairs: b = base right / ft wrong, c = base wrong / ft right
    b = sum(base_r[i]["correct"] and not ft_r[i]["correct"] for i in ids)
    c = sum(ft_r[i]["correct"] and not base_r[i]["correct"] for i in ids)
    p = mcnemar(b, c)
    blo, bhi = wilson(bk, n)
    flo, fhi = wilson(fk, n)
    delta = (fk - bk) / n * 100

    print(f"\n### {name}  (n={n}, paired)")
    print(f"  {base:<22} {bk/n*100:5.1f}%   [{blo*100:.1f}, {bhi*100:.1f}]")
    print(f"  {ft:<22} {fk/n*100:5.1f}%   [{flo*100:.1f}, {fhi*100:.1f}]")
    print(f"  delta                  {delta:+5.1f} pp")
    print(f"  discordant: base-only={b}  ft-only={c}   McNemar p={p:.3f}")
    verdict = ("no significant change" if p >= 0.05
               else ("REGRESSION" if b > c else "improvement"))
    print(f"  -> {verdict} at alpha=0.05")
    return {"n": n, "base_acc": bk / n, "ft_acc": fk / n, "delta_pp": delta,
            "mcnemar_p": p, "verdict": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--finetuned", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()

    bs, br = load(a.base)
    fs, fr = load(a.finetuned)
    bl, fl = bs["label"], fs["label"]

    print(f"Capability retention: {fl} vs {bl}")
    for lbl, s in ((bl, bs), (fl, fs)):
        print(f"  {lbl}: median {s['median_gen_tok']} generated tokens")
        for k in ("gpqa_diamond", "mmlu_pro"):
            d = s[k]
            print(f"    {k}: {d['truncated']}/{d['n']} truncated, "
                  f"{d['unparsed']}/{d['n']} unparseable")
    # A truncation-rate gap between the models is itself a finding, and it
    # makes the raw accuracy comparison unfair -- hence the finished-only pass.
    for k in ("gpqa_diamond", "mmlu_pro"):
        gap = abs(bs[k]["truncated"] / max(bs[k]["n"], 1)
                  - fs[k]["truncated"] / max(fs[k]["n"], 1))
        if gap > 0.05:
            print(f"  ! {k}: truncation rates differ by {gap*100:.0f}pp between "
                  f"models -- trust the 'both finished' rows below")

    out = {}
    for name, pref in (("GPQA-Diamond", "gpqa"), ("MMLU-Pro", "mmlu")):
        b_sub = {k: v for k, v in br.items() if k.startswith(pref)}
        f_sub = {k: v for k, v in fr.items() if k.startswith(pref)}
        out[pref] = section(name, bl, fl, b_sub, f_sub)
        out[pref + "_finished"] = section(name, bl, fl, b_sub, f_sub,
                                          finished_only=True)

    print("\n### MMLU-Pro by category (descriptive; n per cell is small)")
    cats = sorted({v["group"] for k, v in br.items() if k.startswith("mmlu")})
    for cat in cats:
        ids = [k for k in br if k.startswith("mmlu") and br[k]["group"] == cat and k in fr]
        if not ids:
            continue
        bacc = sum(br[i]["correct"] for i in ids) / len(ids) * 100
        facc = sum(fr[i]["correct"] for i in ids) / len(ids) * 100
        print(f"  {cat:<22} n={len(ids):<4} {bacc:5.1f}% -> {facc:5.1f}%  ({facc-bacc:+5.1f})")

    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
