"""Measure how packed the bachelor timetables are, in the model's units.

Reads data/schedule_events.csv (from tools/schedule_from_review.py) and reports, per
programme year, term and spring period:
  - busy_frac_4h: share of the 10 weekly half-day blocks (Mon-Fri 08-12, 13-17) that
    have any scheduled teaching (the model's `busy_fraction` for 4 h sessions)
  - busy_frac_2h: same for the 20 two-hour slots (08-10, 10-12, 13-15, 15-17)
  - sched_h: scheduled hours per teaching week inside 08-17
  - free_4h_weekly: half-day blocks free in >= REGULAR of the period's teaching weeks
    (a course needs the same slot most weeks), and free_2h_weekly likewise
  - corr: mean pairwise correlation of busy blocks between programme years (the
    model's `busy_correlation`)

Inclusion levels:
  mandatory    courses with category "mandatory" in the cohort's study plan
  all          every course the review links to that programme year (for year 3 this
               is the union of all electives and thesis tracks, so an upper bound)
Writes results/schedule_busy.csv and results/schedule_blocks.csv.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REGULAR = 0.75
BLOCKS_4H = [(8 * 60, 12 * 60), (13 * 60, 17 * 60)]
BLOCKS_2H = [(8 * 60, 10 * 60), (10 * 60, 12 * 60), (13 * 60, 15 * 60), (15 * 60, 17 * 60)]
LEVELS = {"mandatory": {"mandatory"},
          "all": {"mandatory", "conditionallyElective", "recommended", "not in plan"}}


def m(s):
    return int(s[:2]) * 60 + int(s[3:])


def grid(ev: pd.DataFrame, blocks, weeks) -> np.ndarray:
    """weeks x (5 days x len(blocks)) busy matrix for one programme year/period."""
    g = np.zeros((len(weeks), 5 * len(blocks)), bool)
    wi = {w: i for i, w in enumerate(weeks)}
    for r in ev.itertuples():
        if r.weekday > 4:
            continue
        s, e = m(r.start), m(r.end)
        for j, (a, b) in enumerate(blocks):
            if s < b and e > a:
                g[wi[r.week_no], int(r.weekday) * len(blocks) + j] = True
    return g


def heatmap(masks):
    """Share of teaching weeks each half-day block is busy, VT26, mandatory courses."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    labels = ["COPEN1", "CTFYS1", "CTFYS2", "CTMAT1", "CTMAT2", "CFATE1", "CFATE2", "CFATE3"]
    # KTH profile: white -> light blue -> sky blue -> KTH blue -> navy; Figtree
    sys.path.insert(0, str(ROOT))
    from model.figstyle import use_kth_fonts
    use_kth_fonts()
    cmap = LinearSegmentedColormap.from_list("kth", ["#FFFFFF", "#DEF0FF", "#6298D2", "#004791", "#000061"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9), sharey=True)
    fig.set_facecolor("#FFFFFF")
    for ax, period in zip(axes, ["P3", "P4"]):
        M = np.array([masks[(l, "VT26", period, "mandatory")] for l in labels])
        im = ax.imshow(M, cmap=cmap, vmin=0, vmax=1, aspect="auto")
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                if M[i, j] < 0.25:
                    ax.text(j, i, "free", ha="center", va="center", fontsize=7, color="#323232")
        ax.set_xticks(range(10), [f"{d}\n{h}" for d in ["Mon", "Tue", "Wed", "Thu", "Fri"] for h in ["am", "pm"]], fontsize=7.5, color="#323232")
        ax.set_yticks(range(len(labels)), labels, fontsize=8.5, color="#323232")
        ax.set_title(f"VT26 {period}", loc="left", fontsize=10, color="#212121", fontweight=600)
        ax.tick_params(length=0)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_xticks(np.arange(-.5, 10, 1), minor=True)
        ax.set_yticks(np.arange(-.5, len(labels), 1), minor=True)
        ax.grid(which="minor", color="#FFFFFF", linewidth=2)
        ax.tick_params(which="minor", length=0)
    cb = fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02)
    cb.set_label("Share of teaching weeks with mandatory teaching", fontsize=8, color="#323232")
    cb.ax.tick_params(labelsize=7, colors="#6E6E6E", length=0)
    cb.outline.set_visible(False)
    fig.suptitle("Bachelor half-days with mandatory teaching (08-12 / 13-17)", x=0.01, ha="left", fontsize=11.5, color="#004791", fontweight=600)
    (ROOT / "results" / "figures").mkdir(parents=True, exist_ok=True)
    fig.savefig(ROOT / "results" / "figures" / "bachelor_busy_blocks.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def main():
    ev = pd.read_csv(ROOT / "data" / "schedule_events.csv")
    ev = ev[ev.teaching_week]
    rows, masks = [], {}
    for (label, term, period), g0 in ev.groupby(["label", "term", "period"]):
        weeks = sorted(g0.week_no.unique())
        for level, cats in LEVELS.items():
            g = g0[g0.category.isin(cats)]
            g4 = grid(g, BLOCKS_4H, weeks)
            g2 = grid(g, BLOCKS_2H, weeks)
            gg = g
            hrs = sum(max(0, min(m(r.end), 17 * 60) - max(m(r.start), 8 * 60)) for r in gg.itertuples() if r.weekday < 5) / 60
            rows.append({"label": label, "programme": label[:-1], "year": int(label[-1]), "term": term,
                         "period": period, "level": level, "teaching_weeks": len(weeks),
                         "sched_h_per_week": round(hrs / len(weeks), 1),
                         "busy_frac_4h": round(g4.mean(), 3), "busy_frac_2h": round(g2.mean(), 3),
                         "free_4h_weekly": int(((~g4).mean(0) >= REGULAR).sum()),
                         "free_2h_weekly": int(((~g2).mean(0) >= REGULAR).sum()),
                         "busy_4h_by_block": "".join("x" if v >= 0.5 else "." for v in g4.mean(0))})
            masks[(label, term, period, level)] = g4.mean(0)
    df = pd.DataFrame(rows)
    pd.DataFrame([{"label": k[0], "term": k[1], "period": k[2], "level": k[3],
                   **{f"{d}_{h}": round(float(v), 3) for (d, h), v in
                      zip(itertools.product(["Mon", "Tue", "Wed", "Thu", "Fri"], ["am", "pm"]), m_)}}
                  for k, m_ in masks.items()]).to_csv(ROOT / "results" / "schedule_blocks.csv", index=False)
    heatmap(masks)
    df.to_csv(ROOT / "results" / "schedule_busy.csv", index=False)

    # Correlation of weekly busy patterns between programme years (same term/period/level)
    corr = []
    for (term, period, level), _ in df.groupby(["term", "period", "level"]):
        keys = [k for k in masks if k[1:] == (term, period, level)]
        cs = [np.corrcoef(masks[a], masks[b])[0, 1] for a, b in itertools.combinations(keys, 2)
              if masks[a].std() > 0 and masks[b].std() > 0]
        corr.append({"term": term, "period": period, "level": level, "mean_pairwise_corr": round(float(np.mean(cs)), 2)})
    pd.DataFrame(corr).to_csv(ROOT / "results" / "schedule_corr.csv", index=False)

    pd.set_option("display.width", 200)
    for level in LEVELS:
        print(f"\n== {level}")
        print(df[df.level == level].drop(columns=["programme", "year", "level"]).to_string(index=False))
    print(pd.DataFrame(corr).to_string(index=False))


def calibration():
    """Summary used to calibrate data/scenarios.yaml (years 1-2 + COPEN, mandatory courses)."""
    df = pd.read_csv(ROOT / "results" / "schedule_busy.csv")
    d = df[(df.level == "mandatory") & (df.year < 3)]
    corr = pd.read_csv(ROOT / "results" / "schedule_corr.csv")
    out = {
        "cohort_periods": len(d),
        "busy_frac_4h_mean": round(d.busy_frac_4h.mean(), 3),
        "regular_free_halfdays_mean": round(d.free_4h_weekly.mean(), 2),
        "regular_free_halfdays_max": int(d.free_4h_weekly.max()),
        "busy_equiv_4h": round(1 - d.free_4h_weekly.mean() / 10, 3),
        "busy_frac_2h_mean": round(d.busy_frac_2h.mean(), 3),
        "regular_free_2h_mean": round(d.free_2h_weekly.mean(), 2),
        "busy_equiv_2h": round(1 - d.free_2h_weekly.mean() / 20, 3),
        "sched_h_per_week_mean": round(d.sched_h_per_week.mean(), 1),
        "pairwise_corr_mandatory": corr[corr.level == "mandatory"].mean_pairwise_corr.tolist(),
    }
    import json
    (ROOT / "results" / "schedule_calibration.json").write_text(json.dumps(out, indent=1))
    print(out)


if __name__ == "__main__":
    main()
    calibration()
