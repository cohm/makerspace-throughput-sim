"""Run all model layers and write results/, figures and web/defaults.json.

Usage:  .venv/bin/python run.py [--draws N] [--quick]
Then:   .venv/bin/python report.py   (writes report.md from results/)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from model.figstyle import use_kth_fonts
from model.capacity import analytic, parallel_sections, room_hours_per_period, with_sections
from model.inputs import build_cohorts, load_yaml, scenario_names, scenario_params
from model.open_access import machines, open_access
from model.schedule import delivered_hours, feasible_hours

ROOT = Path(__file__).resolve().parent
RES = ROOT / "results"
FIG = RES / "figures"

# KTH graphical profile (cohm/kth-doc-templates): KTH blue, brick, sky blue, yellow dark, in
# this order. Validated with the dataviz palette checker: CVD and normal-vision separation
# pass for all pairs; KTH blue sits just below the lightness band (L 0.41), kept as brand primary.
SERIES = ["#004791", "#E86A58", "#6298D2", "#A65900"]
INK, INK2, MUTED, GRID, BASE, SURF = "#212121", "#323232", "#6E6E6E", "#E6E6E6", "#A5A5A5", "#FFFFFF"
use_kth_fonts()
SECTION_SIZES = [16, 24, 32, 48]
PLANNED = 16   # h/group every course plans, for the "what do groups actually get" metric


def style(ax, title, xlabel, ylabel):
    ax.set_facecolor(SURF)
    ax.figure.set_facecolor(SURF)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(BASE)
    ax.tick_params(colors=MUTED, length=0, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", color=SERIES[0], fontsize=11.5, fontweight=600, pad=12)
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def set_path(d: dict, path: str, value):
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = value


def run_scenarios(cohorts, fac, draws):
    rows, success, curves = [], [], []
    for name in scenario_names():
        p = scenario_params(name)
        a = analytic(cohorts, fac, p)
        r = feasible_hours(cohorts, fac, p, draws)
        L = p["course"]["session_hours"]
        nxt = dict(r["curve"]).get(r["Y"] + L)
        d = delivered_hours(cohorts, fac, p, PLANNED, draws)
        rows.append({"scenario": name, "label": p["label"], "description": p["description"],
                     "Y_fluid_bound": round(a["Y_max"], 2), "Y_sim": r["Y"],
                     "share_served_at_Y": round(dict(r["curve"]).get(r["Y"], 0), 3),
                     "share_served_at_Y_plus_session": None if nxt is None else round(nxt, 3),
                     f"mean_delivered_if_plan_{PLANNED}h": round(d["mean"], 1),
                     f"p5_delivered_if_plan_{PLANNED}h": round(d["p5"], 1)})
        curves += [{"scenario": name, "label": p["label"], "Y": y, "share_served": round(s, 4)}
                   for y, s in r["curve"]]
        success += [{"scenario": name, "cohort": c, "p_served_at_Y": round(v, 3),
                     f"mean_h_if_plan_{PLANNED}h": round(h, 1)}
                    for c, v, h in zip(cohorts["cohort"], r["cohort_success"], d["per_cohort_mean"])]
    return pd.DataFrame(rows), pd.DataFrame(curves), pd.DataFrame(success)


def run_section_sweep(cohorts, fac, draws):
    rows = []
    for S in SECTION_SIZES:
        for name in scenario_names():
            p = scenario_params(name, {"course": {"section_size": S}})
            a = analytic(cohorts, fac, p)
            r = feasible_hours(cohorts, fac, p, draws)
            rows.append({"section_size": S, "scenario": name, "label": p["label"],
                         "parallel_sections": a["parallel_sections"], "sections": a["total_sections"],
                         "Y_fluid_bound": round(a["Y_max"], 2), "Y_sim": r["Y"]})
    return pd.DataFrame(rows)


def run_open_access(cohorts, fac):
    p = scenario_params("optimal")
    st = fac["staffing"]
    rows = []
    for variant in ("documented", "extended"):
        hv = fac["hours"]["open_access_variants"][variant]
        wanted = 5 * hv["evening_h_per_weekday"] + hv["weekend_h_per_week"]
        need_staff = wanted * st["staff_per_open_hour"] / (st["fte_hours_per_week"] * st["availability"] * st["staff_supervision_share"])
        need_sa = wanted * st["sa_per_open_hour"] / (st["fte_hours_per_week"] * st["availability"])
        for staffing in ("budgeted", "matched"):
            f = copy.deepcopy(fac)
            if staffing == "matched":
                f["staffing"]["staff_fte"] = max(st["staff_fte"], need_staff)
                f["staffing"]["student_assistant_fte"] = max(st["student_assistant_fte"], need_sa)
            pp = scenario_params("optimal", {"open_access": {"hours_variant": variant}})
            o = open_access(cohorts, f, pp)
            rows.append({"hours_variant": variant, "staffing": staffing,
                         "staff_fte": round(f["staffing"]["staff_fte"], 2),
                         "sa_fte": round(f["staffing"]["student_assistant_fte"], 2),
                         "open_h_per_week_wanted": o["open_wanted"],
                         "open_h_per_week_staffed": round(o["open_hours"], 1),
                         "binding": o["open_binding"], "simultaneous_users": o["simultaneous_users"],
                         "Z": round(o["Z"], 1)})
    return pd.DataFrame(rows), machines(cohorts, fac, p)


SENS_FLUID = [  # (label, target, path, low, high)  -> effect on fluid Y_max (optimal scenario)
    ("Cohort sizes x0.8 / x1.2", "scale", None, 1.2, 0.8),
    ("Section size 32 / 16", "p", "course.section_size", 32, 16),
    ("Booking efficiency 0.75 / 0.95", "p", "room.booking_efficiency", 0.75, 0.95),
    ("Other daytime use 20% / 0%", "p", "room.other_daytime_use", 0.2, 0.0),
    ("Teaching weeks/period 6 / 8", "p", "calendar.teaching_weeks_per_period", 6, 8),
]
SENS_OPEN = [  # -> effect on Z
    ("Cohort sizes x1.2 / x0.8", "scale", None, 1.2, 0.8),
    ("Occupancy 0.4 / 0.8", "p", "open_access.occupancy", 0.4, 0.8),
    ("Non-course share 50% / 10%", "p", "open_access.noncourse_share", 0.5, 0.1),
    ("Persons per group visit 3 / 1", "p", "open_access.persons_present_per_group", 3, 1),
    ("Staff time on supervision 30% / 70%", "fac", "staffing.staff_supervision_share", 0.3, 0.7),
    ("Users per supervisor 10 / 30", "fac", "staffing.max_users_per_supervisor", 10, 30),
]
SENS_TIMETABLE = [  # -> simulated Y, coordinated scenario
    ("Bachelor: half-days the course owns 1 / 4", "timetable.by_level.bachelor.min_free_blocks", 1, 4),
    ("Bachelor busy 0.97 / 0.70 (flexible weekly)", "timetable.by_level.bachelor.busy_fraction", 0.97, 0.70),
    ("Master busy fraction 0.8 / 0.5 (assumed)", "timetable.busy_fraction", 0.8, 0.5),
    ("Project weeks 4 / 7", "timetable.project_weeks", 4, 7),
    ("Busy correlation 0.6 / 0.0", "timetable.busy_correlation", 0.6, 0.0),
    ("Master: half-days the course owns 1 / 3", "timetable.min_free_blocks", 1, 3),
    ("Max sessions/week 1 / 3", "course.max_sessions_per_week", 1, 3),
]


def run_sensitivity(cohorts, fac, draws):
    rows = []
    for metric, table, scen in (("Y_fluid", SENS_FLUID, "optimal"), ("Z", SENS_OPEN, "optimal")):
        base_p = scenario_params(scen)
        base = analytic(cohorts, fac, base_p)["Y_max"] if metric == "Y_fluid" else open_access(cohorts, fac, base_p)["Z"]
        for label, target, path, lo, hi in table:
            vals = []
            for v in (lo, hi):
                f, p, co = copy.deepcopy(fac), scenario_params(scen), cohorts
                if target == "scale":
                    co = build_cohorts(scale=v)
                elif target == "p":
                    set_path(p, path, v)
                else:
                    set_path(f, path, v)
                vals.append(analytic(co, f, p)["Y_max"] if metric == "Y_fluid" else open_access(co, f, p)["Z"])
            rows.append({"metric": metric, "parameter": label, "base": round(base, 2),
                         "low_case": round(vals[0], 2), "high_case": round(vals[1], 2)})
    base = delivered_hours(cohorts, fac, scenario_params("coordinated"), PLANNED, draws)["mean"]
    for label, path, lo, hi in SENS_TIMETABLE:
        vals = []
        for v in (lo, hi):
            p = scenario_params("coordinated")
            set_path(p, path, v)
            vals.append(delivered_hours(cohorts, fac, p, PLANNED, draws)["mean"])
        rows.append({"metric": "delivered_coordinated", "parameter": label, "base": round(base, 2),
                     "low_case": round(vals[0], 2), "high_case": round(vals[1], 2)})
    return pd.DataFrame(rows)


TIMETABLE_VARIANTS = [  # (label, overrides on the coordinated scenario)
    ("Before calibration: bachelor 70% busy, varying week to week", {"timetable": {"by_level": {"bachelor": {"busy_fraction": 0.7}}}}),
    ("Measured, recurring weekly slot, course owns 2 half-days (default)", {}),
    ("Measured, course owns 1 half-day", {"timetable": {"by_level": {"bachelor": {"min_free_blocks": 1}}}}),
    ("Measured, course owns 3 half-days", {"timetable": {"by_level": {"bachelor": {"min_free_blocks": 3}}}}),
    ("Measured, course owns 4 half-days", {"timetable": {"by_level": {"bachelor": {"min_free_blocks": 4}}}}),
    ("Measured, 2 h sessions in timetable gaps (course owns 3 x 2 h)",
     {"course": {"session_hours": 2, "max_sessions_per_week": 4},
      "timetable": {"min_free_blocks": 4, "by_level": {"bachelor": {"busy_fraction": 0.68, "min_free_blocks": 3}}}}),
]


def run_timetable_variants(cohorts, fac, draws):
    rows = []
    big = ["CTFYS-1", "CTFYS-2", "CFATE-1", "COPEN-1"]
    for label, ex in TIMETABLE_VARIANTS:
        p = scenario_params("coordinated", ex)
        r = feasible_hours(cohorts, fac, p, draws)
        d = delivered_hours(cohorts, fac, p, PLANNED, draws)
        pc = dict(zip(cohorts["cohort"], d["per_cohort_mean"]))
        L = p["course"]["session_hours"]
        curve = dict(r["curve"])
        lv = dict(zip(cohorts["cohort"], cohorts["level"]))
        w = dict(zip(cohorts["cohort"], cohorts["students"]))
        def wmean(keys):
            return sum(pc[k] * w[k] for k in keys) / sum(w[k] for k in keys)
        rows.append({"variant": label, "guaranteed_Y": r["Y"],
                     "share_served_at_8h": round(curve.get(8, float("nan")), 3) if 8 % L == 0 else None,
                     f"mean_if_plan_{PLANNED}h": round(d["mean"], 1), "p5": round(d["p5"], 1),
                     "bachelor_mean": round(wmean([k for k in pc if lv[k] == "bachelor"]), 1),
                     "big_bachelor_mean": round(wmean(big), 1),
                     "master_mean": round(wmean([k for k in pc if lv[k] == "master"]), 1)})
    return pd.DataFrame(rows)


def _plan_variants(plans):
    def mod(changes):
        pl = copy.deepcopy(plans)
        for (key, field), v in changes.items():
            pl[key][field] = v
        return pl
    return [
        ("As proposed: SK1105 8 h, SE1055 8 h, year-3 device 12 h", plans),
        ("SK1105 at 12 h", mod({("CTFYS-1", "planned_hours"): 12})),
        ("Year-3 course owns 3 half-days", mod({("CTFYS-3", "own_halfdays"): 3})),
        ("Year-3 course owns 4 half-days", mod({("CTFYS-3", "own_halfdays"): 4})),
        ("Year-3 owns 3 half-days, project over all 7 weeks",
         mod({("CTFYS-3", "own_halfdays"): 3, ("CTFYS-3", "weeks"): [1, 7]})),
    ]


def run_course_plans(cohorts, fac, draws):
    """Concrete CTFYS proposals (data/course_plans.yaml) inside the coordinated scenario,
    with every other programme-year planning 8, 12 or 16 h."""
    sets = load_yaml("course_plans.yaml")["plan_sets"]
    plans = sets["ctfys_proposal"]["plans"]
    keys = list(plans)
    rows = []
    for others in (8, 12, 16):
        for label, pl in _plan_variants(plans):
            p = scenario_params("coordinated")
            p["course_plans"] = pl
            d = delivered_hours(cohorts, fac, p, others, draws)
            pc = dict(zip(cohorts["cohort"], d["per_cohort_mean"]))
            sv = dict(zip(cohorts["cohort"], d["per_cohort_served"]))
            rq = dict(zip(cohorts["cohort"], d["requested_per_cohort"]))
            row = {"others_plan_h": others, "variant": label, "mean_all": round(d["mean"], 1)}
            for k in keys:
                row[f"{k}_planned"] = int(rq[k])
                row[f"{k}_mean"] = round(pc[k], 1)
                row[f"{k}_served"] = round(sv[k], 2)
            rows.append(row)
    return pd.DataFrame(rows)


def fig_sweep(sweep: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    names = scenario_names()
    w = 0.8 / len(names)
    for i, n in enumerate(names):
        d = sweep[sweep.scenario == n]
        xs = [SECTION_SIZES.index(s) + (i - (len(names) - 1) / 2) * w for s in d.section_size]
        ax.bar(xs, d.Y_sim, width=w - 0.03, color=SERIES[i], label=d.label.iloc[0], zorder=2)
        for x, y in zip(xs, d.Y_sim):
            if y == 0:
                ax.text(x, 0.2, "0", ha="center", va="bottom", color=INK2, fontsize=8)
    fl = sweep[sweep.scenario == "optimal"]
    ax.hlines(fl.Y_fluid_bound, [SECTION_SIZES.index(s) - 0.42 for s in fl.section_size],
              [SECTION_SIZES.index(s) + 0.42 for s in fl.section_size],
              color=INK2, lw=1.2, ls="--", zorder=3, label="Fluid upper bound")
    ax.set_xticks(range(len(SECTION_SIZES)), [f"{s} students\n({k} in parallel)" for s, k in
                                               zip(SECTION_SIZES, fl.parallel_sections)])
    style(ax, "Scheduled makerspace hours per group, one course per programme-year",
          "Section size (sessions running in parallel in 3203 + side rooms)", "Hours per group per year")
    ax.set_ylim(0, max(fl.Y_fluid_bound) * 1.08)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, ncol=5, loc="upper center",
              bbox_to_anchor=(0.5, -0.2), columnspacing=1.2, handlelength=1.6)
    fig.tight_layout()
    fig.savefig(FIG / "hours_by_section_size.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def fig_curves(curves: pd.DataFrame, thr: float):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for i, n in enumerate(scenario_names()):
        d = curves[curves.scenario == n]
        ax.plot(d.Y, d.share_served * 100, color=SERIES[i], lw=2, marker="o", ms=5, label=d.label.iloc[0])
    ax.axhline(thr * 100, color=BASE, lw=1, ls="--")
    ax.text(ax.get_xlim()[1], thr * 100 + 1, f"{thr:.0%} target", color=MUTED, fontsize=8, ha="right")
    style(ax, "Share of programme-years that get all their sessions (section size 24)",
          "Scheduled hours per group (4 h sessions)", "Programme-years served (%)")
    ax.set_ylim(0, 102)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower left")
    fig.tight_layout()
    fig.savefig(FIG / "share_served.png", dpi=160)
    plt.close(fig)


def fig_tornado(sens: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.8))
    for ax, metric, title in ((axes[0], "Y_fluid", "Scheduled h/group, fluid bound"),
                              (axes[1], "delivered_coordinated", f"Scheduled h/group delivered,\ncoordinated, if all plan {PLANNED} h"),
                              (axes[2], "Z", "Open-access h/group")):
        d = sens[sens.metric == metric].copy()
        d["span"] = (d.high_case - d.low_case).abs()
        d = d.sort_values("span")
        base = d.base.iloc[0]
        for j, r in enumerate(d.itertuples()):
            lo, hi = sorted([r.low_case, r.high_case])
            ax.barh(j, hi - lo, left=lo, color=SERIES[0], height=0.55, zorder=2)
        ax.axvline(base, color=INK2, lw=1)
        ax.set_yticks(range(len(d)), d.parameter, fontsize=8)
        style(ax, title, "Hours per group per year", "")
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(FIG / "sensitivity.png", dpi=160)
    plt.close(fig)


def sim_export(cohorts):
    """Course-simulation data for web/sim.js: courses with real names and syllabus LAB/PRO-type
    modules (data/kth_courses/), takers and cohort sizes. Activities are mock examples."""
    sim = load_yaml("sim_courses.yaml")
    kc = ROOT / "data" / "kth_courses"
    names, mods = {}, {}
    if (kc / "curricula.csv").exists():
        cu = pd.read_csv(kc / "curricula.csv", dtype=str).fillna("")
        names = dict(zip(cu.course, cu.name))
        mo = pd.read_csv(kc / "course_modules.csv", dtype=str).fillna("")
        lab = mo[mo.module.str.match(r"^(LAB|PRO|PRA|PRJ)") | mo.module_name.str.contains(r"[Ll]ab|[Pp]roject", regex=True)]
        mods = lab.groupby("course").module.apply(lambda m: sorted(set(m))).to_dict()
    level = dict(zip(cohorts["cohort"], cohorts["level"]))
    courses = []
    for c in sim["courses"]:
        c = dict(c)
        c["course_name"] = names.get(c["code"], "")
        c["modules"] = mods.get(c["code"], [])
        c["level"] = level.get(c["takers"][0]["cohort"], "bachelor")
        courses.append(c)
    return {"calendar": sim["calendar"], "rules": sim["rules"], "courses": courses,
            "cohort_sizes": {k: int(v) for k, v in zip(cohorts["cohort"], cohorts["students"])}}


def export_web(cohorts, fac):
    p = scenario_params("optimal")
    cfg = load_yaml("scenarios.yaml")
    out = {"cohorts": cohorts[["cohort", "programme", "name", "year", "level", "students"]].to_dict("records"),
           "facility": fac, "base": cfg["base"],
           "scenarios": {k: {kk: vv for kk, vv in v.items()} for k, v in cfg["scenarios"].items()},
           "check": {"analytic": {k: (float(v) if not isinstance(v, int) else v)
                                  for k, v in analytic(cohorts, fac, p).items()},
                     "Z": float(open_access(cohorts, fac, p)["Z"])}}
    out["sim"] = sim_export(cohorts)
    web = ROOT / "web"
    (web / "defaults.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    # Self-contained page: inline the JS model and the defaults into the template.
    html = (web / "template.html").read_text(encoding="utf-8")
    html = html.replace("/*__MODEL__*/", (web / "model.js").read_text(encoding="utf-8"))
    html = html.replace("/*__SIM__*/", (web / "sim.js").read_text(encoding="utf-8"))
    html = html.replace("/*__DEFAULTS__*/null", json.dumps(out, ensure_ascii=False))
    page = ascii_only(html)
    (web / "index.html").write_text(page, encoding="ascii")
    # GitHub Pages copy (served from /docs on the main branch). The artifact host adds the
    # document skeleton itself; a standalone page needs its own (standards mode, viewport).
    (ROOT / "docs").mkdir(exist_ok=True)
    cut = page.index('<div class="wrap">')          # title, font links and page CSS go in <head>
    head = page[:cut].replace('<meta charset="utf-8">\n', "")
    standalone = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                  '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
                  'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
                  + head + '</head>\n<body>\n' + page[cut:] + '\n</body>\n</html>\n')
    (ROOT / "docs" / "index.html").write_text(standalone, encoding="ascii")


def ascii_only(html: str) -> str:
    """Make the page pure ASCII so it renders the same whatever charset a viewer assumes:
    non-ASCII becomes \\uXXXX escapes inside <script> and &#x...; entities elsewhere."""
    def js_escape(c: str) -> str:
        b = c.encode("utf-16-be")                      # surrogate pairs for chars > U+FFFF
        return "".join(f"\\u{int.from_bytes(b[i:i + 2], 'big'):04x}" for i in range(0, len(b), 2))

    def js(t: str) -> str:
        return "".join(c if ord(c) < 128 else js_escape(c) for c in t)

    def ent(t: str) -> str:
        return "".join(c if ord(c) < 128 else f"&#x{ord(c):x};" for c in t)

    parts = re.split(r"(<script>.*?</script>)", html, flags=re.S)
    return "".join(js(part) if part.startswith("<script>") else ent(part) for part in parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=None, help="Monte Carlo draws for headline scenarios")
    ap.add_argument("--quick", action="store_true", help="fewer draws everywhere (for testing)")
    a = ap.parse_args()
    draws = a.draws or (30 if a.quick else scenario_params("optimal")["sim"]["draws"])
    sweep_draws = 20 if a.quick else 80

    RES.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    fac = load_yaml("facility.yaml")
    cohorts = build_cohorts()
    p0 = scenario_params("optimal")
    with_sections(cohorts, p0).to_csv(RES / "cohorts.csv", index=False)

    scen, curves, success = run_scenarios(cohorts, fac, draws)
    sweep = run_section_sweep(cohorts, fac, sweep_draws)
    oa, mach = run_open_access(cohorts, fac)
    sens = run_sensitivity(cohorts, fac, sweep_draws)
    tvar = run_timetable_variants(cohorts, fac, sweep_draws)
    tvar.to_csv(RES / "timetable_variants.csv", index=False)
    cplans = run_course_plans(cohorts, fac, sweep_draws)
    cplans.to_csv(RES / "course_plans.csv", index=False)

    scen.to_csv(RES / "scenarios.csv", index=False)
    curves.to_csv(RES / "share_served_curves.csv", index=False)
    success.to_csv(RES / "cohort_success.csv", index=False)
    sweep.to_csv(RES / "section_size_sweep.csv", index=False)
    oa.to_csv(RES / "open_access.csv", index=False)
    pd.DataFrame(mach).round(2).to_csv(RES / "machines.csv", index=False)
    sens.to_csv(RES / "sensitivity.csv", index=False)
    meta = {"draws": draws, "sweep_draws": sweep_draws,
            "parallel_sections": parallel_sections(fac, p0),
            "room_hours_per_period": room_hours_per_period(fac, p0),
            "analytic": {k: float(v) for k, v in analytic(cohorts, fac, p0).items()},
            "open_access": {k: (float(v) if not isinstance(v, str) else v)
                            for k, v in open_access(cohorts, fac, p0).items()}}
    (RES / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    fig_sweep(sweep)
    fig_curves(curves, p0["sim"]["success_threshold"])
    fig_tornado(sens)
    export_web(cohorts, fac)

    pd.set_option("display.width", 160)
    print(scen.drop(columns="description").to_string(index=False))
    print(sweep.pivot(index="section_size", columns="scenario", values="Y_sim").to_string())
    print(oa.to_string(index=False))
    print(pd.DataFrame(mach).round(2).to_string(index=False))
    print(sens.to_string(index=False))
    print(tvar.to_string(index=False))
    print(cplans.to_string(index=False))


if __name__ == "__main__":
    main()
