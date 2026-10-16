"""Write report.md from results/ (run `run.py` first)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from model.inputs import load_yaml, scenario_params

ROOT = Path(__file__).resolve().parent
RES = ROOT / "results"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |")
    return "\n".join(out)


def main():
    meta = json.loads((RES / "meta.json").read_text())
    co = pd.read_csv(RES / "cohorts.csv")
    sc = pd.read_csv(RES / "scenarios.csv").set_index("scenario")
    sweep = pd.read_csv(RES / "section_size_sweep.csv")
    oa = pd.read_csv(RES / "open_access.csv")
    mach = pd.read_csv(RES / "machines.csv")
    sens = pd.read_csv(RES / "sensitivity.csv")
    succ = pd.read_csv(RES / "cohort_success.csv")
    tvar = pd.read_csv(RES / "timetable_variants.csv")
    cal = json.loads((RES / "schedule_calibration.json").read_text())
    busy = pd.read_csv(RES / "schedule_busy.csv")
    cp = pd.read_csv(RES / "course_plans.csv")
    p = scenario_params("optimal")
    fac = load_yaml("facility.yaml")
    a = {k: (int(v) if float(v).is_integer() else round(v, 2)) for k, v in meta["analytic"].items()}
    a["Y_max"] = round(meta["analytic"]["Y_max"], 2)
    o = meta["open_access"]
    planned_col = [c for c in sc.columns if c.startswith("mean_delivered_if_plan_")][0]
    p5_col = [c for c in sc.columns if c.startswith("p5_delivered_if_plan_")][0]
    planned = int(planned_col.split("_")[-1].rstrip("h"))
    S, L = p["course"]["section_size"], p["course"]["session_hours"]
    nb = co[co.level == "bachelor"]
    nm = co[co.level == "master"]

    def Y(s):
        return int(sc.loc[s, "Y_sim"])

    def guar(s):
        share = sc.loc[s, "share_served_at_Y"]
        note = f" (borderline: {share:.1%} of programme-years served)" if share < 0.97 else ""
        return f"≈**{Y(s)} h guaranteed**{note}"

    def mean(s):
        return sc.loc[s, planned_col]

    def p5(s):
        return sc.loc[s, p5_col]

    oa_i = None  # set after renaming
    cnc = mach.set_index("resource").loc["CNC"]
    pr = mach.set_index("resource").loc["3D printers"]

    # Per-cohort table: mean hours delivered if every course plans `planned` h
    piv = succ.pivot(index="cohort", columns="scenario", values=f"mean_h_if_plan_{planned}h")
    per = co.set_index("cohort")[["name", "students", "sections", "groups"]].join(piv)
    per = per[["name", "students", "sections", "groups", "optimal", "coordinated", "uncoordinated", "concentrated"]]
    per.columns = ["Programme", "Students", f"Sections (S={S})", "Groups", "Optimal",
                   "Coordinated", "Uncoordinated", "Concentrated P1-P2"]
    per = per.reset_index().rename(columns={"cohort": "Cohort"})
    worst = per.sort_values("Concentrated P1-P2").head(5)["Cohort"].tolist()

    sw = sweep.pivot(index="section_size", columns="scenario", values="Y_sim")[
        ["optimal", "coordinated", "uncoordinated", "concentrated"]]
    sw.insert(0, "Fluid bound", sweep[sweep.scenario == "optimal"].set_index("section_size")["Y_fluid_bound"])
    sw.insert(0, "Parallel", sweep[sweep.scenario == "optimal"].set_index("section_size")["parallel_sections"])
    sw.insert(0, "Sections", sweep[sweep.scenario == "optimal"].set_index("section_size")["sections"])
    sw = sw.reset_index().rename(columns={"section_size": "Section size"})

    stab = pd.DataFrame({
        "Scenario": sc["label"],
        "What it assumes": sc["description"],
        "Guaranteed Y (h/group, ≥95% of programme-years)": sc["Y_sim"].astype(int),
        f"Mean h/group if every course plans {planned} h": sc[planned_col],
        "Worst 5% get (h)": sc[p5_col],
    }).reset_index(drop=True)

    sens_t = sens.copy()
    sens_t["metric"] = sens_t["metric"].map({
        "Y_fluid": "Scheduled, fluid bound", "Z": "Open access Z",
        "delivered_coordinated": f"Scheduled, delivered (coordinated, plan {planned} h)"})
    sens_t.columns = ["Output", "Parameter (low case / high case)", "Base", "Low case", "High case"]

    tv = tvar.set_index("variant")
    tv_default = tv.iloc[1]
    bt = busy[busy.level == "mandatory"].groupby("label").agg(
        sched=("sched_h_per_week", "mean"), busy4=("busy_frac_4h", "mean"),
        free4=("free_4h_weekly", "mean"), free2=("free_2h_weekly", "mean")).reindex(
        ["COPEN1", "CTFYS1", "CTFYS2", "CTFYS3", "CTMAT1", "CTMAT2", "CTMAT3", "CFATE1", "CFATE2", "CFATE3"])
    bt = pd.DataFrame({"Programme-year": bt.index,
                       "Scheduled h/week (08–17)": bt.sched.round(1).values,
                       "Half-days with teaching": (bt.busy4 * 100).round().astype(int).astype(str).add("%").values,
                       "Half-days free in ≥75% of weeks (of 10)": bt.free4.round(1).values,
                       "2 h slots free in ≥75% of weeks (of 20)": bt.free2.round(1).values})
    tvar_t = tvar.rename(columns={"variant": "Bachelor timetable assumption (coordinated scenario)",
                                  "guaranteed_Y": "Guaranteed h", "share_served_at_8h": "Served at 8 h",
                                  f"mean_if_plan_{planned}h": f"Mean if plan {planned} h", "p5": "Worst 5%",
                                  "bachelor_mean": "Bachelor mean", "big_bachelor_mean": "CTFYS1-2, CFATE1, COPEN mean",
                                  "master_mean": "Master mean"})
    def cell(r, k):
        return f"{r[k + '_mean']:.1f} of {r[k + '_planned']} h ({r[k + '_served']:.0%})"
    cp_t = pd.DataFrame({
        "Others plan": cp.others_plan_h.astype(str) + " h",
        "Variant": cp.variant,
        "CTFYS1: SK1105": [cell(r, "CTFYS-1") for _, r in cp.iterrows()],
        "CTFYS2: SE1055": [cell(r, "CTFYS-2") for _, r in cp.iterrows()],
        "CTFYS3: measurement device": [cell(r, "CTFYS-3") for _, r in cp.iterrows()],
    })
    cpi = cp.set_index(["others_plan_h", "variant"])
    v0 = cp.variant.iloc[0]
    vbest = "Year-3 owns 3 half-days, project over all 7 weeks"

    def g(o, v, k, what):
        return cpi.loc[(o, v), f"{k}_{what}"]

    oa_t = oa.rename(columns={"hours_variant": "Opening hours", "staffing": "Staffing",
                              "staff_fte": "Staff FTE", "sa_fte": "Student-assistant FTE",
                              "open_h_per_week_wanted": "Open h/week wanted",
                              "open_h_per_week_staffed": "Open h/week staffed",
                              "binding": "Binding limit", "simultaneous_users": "Max users at once",
                              "Z": "Z (h/group/yr)"})
    oa_i = oa_t.set_index(["Opening hours", "Staffing"])
    mach_t = mach.rename(columns={"resource": "Resource", "supply_h": "Supply h/yr", "demand_h": "Demand h/yr",
                                  "utilisation": "Utilisation", "max_h_per_using_group": "Max h per using group"})

    text = f"""# SCI Makerspace: first-order throughput estimate

*Generated by `report.py` from `results/` (run `run.py` first). Monte Carlo draws:
{meta['draws']} for headline scenarios, {meta['sweep_draws']} for sweeps and sensitivity.
All inputs are in `data/*.yaml`, each tagged with its source or marked as assumed.*

## Headline

The target is one makerspace project course per year for every SCI programme-year:
CTFYS, CTMAT and CFATE years 1–3, COPEN, and all 11 SCI master programmes in both years.
That is **{len(co)} programme-years, about {a['total_students']:,} students, {a['total_groups']} project
groups of {p['course']['group_size']}**, taught in supervised sections of {S}. Room 3203 plus
the 3202 and 3D-studio side rooms takes {a['parallel_sections']} such sections at once, in
{L} h daytime blocks.

> **Scheduled, supervised time (weekdays 08–17):**
> - **Spread optimally** over P1–P4 and the week: each programme-year can run
>   **1 course with ≈{Y('optimal')} h of makerspace time per group** (fluid upper bound
>   {a['Y_max']} h). This is the same capacity as 2 courses × {Y('optimal')//2} h.
> - **Realistic timetables, school-coordinated periods:** {guar('coordinated')}.
>   If every course plans {planned} h, groups get on average {mean('coordinated')} h, and the
>   worst-served 5% get {p5('coordinated')} h.
> - **Each programme picks its own period (uncoordinated):** {guar('uncoordinated')}.
>   The average is {mean('uncoordinated')} h, the worst 5% get {p5('uncoordinated')} h.
> - **Concentrated in P1–P2:** {guar('concentrated')}. The average is
>   {mean('concentrated')} h, the worst 5% get {p5('concentrated')} h.
>
> **Self-booked open access (evenings and Saturdays):** about **{o['Z']:.0f} h per group per year**
> on top of the scheduled time. This is limited by **makerspace staff hours**, not by space.
> The budgeted 1.3 FTE staff can cover about {o['open_hours']:.0f} of the {o['open_wanted']:.0f}
> documented open hours per week.

Put simply: under realistic conditions each programme-year can count on **{Y('coordinated')}–{mean('coordinated'):.0f} h of
scheduled room time per group per year** (more only with school-level coordination and
projects spread through the period), **plus about {o['Z']:.0f} h** of open access. That can
be one course or two shorter ones. Even with perfect scheduling the planned space gives
under {a['Y_max']:.0f} h of supervised time per group if every programme-year is to have a course.

**Bachelor timetables are packed, and this is now measured.** VT26 and VT27-draft TimeEdit
data for years 1–2 show teaching in {cal['busy_frac_4h_mean']:.0%} of half-days on average,
but it moves from week to week. Only {cal['regular_free_halfdays_mean']:.1f} of 10 half-days
are free in most weeks. A makerspace course therefore has to use **its own timetable
slots** (a median of about 2 half-days a week for a spring course), not free time. This
mostly lowers the guarantee and hits the big cohorts: CTFYS1–2, CFATE1 and COPEN groups get
{tv_default['big_bachelor_mean']} h on average, against {tv_default['master_mean']} h for the
masters, whose timetables are still assumed. Giving the makerspace course **3–4 half-days**
of its own, or using **2 h sessions** in timetable gaps, brings the guarantee back to about
8 h (see "Bachelor timetables" below).

"Guaranteed" means at least {p['sim']['success_threshold']:.0%} of programme-years get every
session they planned. Sessions are {L} h blocks, so guaranteed values move in {L} h steps.
"Mean if every course plans {planned} h" is the hours actually delivered when every course
asks for {planned} h and the timetable squeezes some of them.

![Hours by section size](results/figures/hours_by_section_size.png)

## Scenarios (section size {S})

{md_table(stab)}

![Share served](results/figures/share_served.png)

**Where the capacity goes.** Starting from the optimal case, these are the steps that cost
the most (see `results/sensitivity.csv`):

1. **Project work bunched in the last weeks of each period.** If every course uses only
   the last {scenario_params('coordinated')['timetable']['project_weeks']} of
   {p['calendar']['teaching_weeks_per_period']} weeks, the first weeks of every period
   stand empty. This is the largest single loss.
2. **Busy cohort timetables.** Bachelor cohorts years 1–2 are effectively free only in the
   makerspace course's own slots (measured, see below). Master cohorts are assumed free in
   about 30% of daytime blocks. Free blocks partly coincide between programmes.
3. **No period coordination, or an autumn peak.** This makes period loads uneven. It hits
   the large cohorts hardest: {', '.join(worst)} fare worst in the concentrated scenario.
   Each needs 5–6 sections squeezed into the cohort's few free half-days.

## Bachelor timetables (measured)

Data source: the schedule reviews at
<https://cohm.github.io/kth-schedule-review/docs/examples/>.
- Every teaching occasion for CTFYS, CTMAT and CFATE years 1–3 and COPEN, VT26 and the
  VT27 draft, from TimeEdit.
- Course categories come from the study plans in `cohm/ProgramVisualization`.
- Parsed by `tools/schedule_from_review.py` and measured by `tools/schedule_analysis.py`.
- Teaching weeks only; exams and exam weeks excluded. Mandatory courses, averaged over
  P3/P4 and both terms.

{md_table(bt)}

![Busy half-days](results/figures/bachelor_busy_blocks.png)

- **Years 1–2 and COPEN: scheduled almost every half-day.** Mandatory teaching averages
  {cal['sched_h_per_week_mean']} h/week. The schedule is not dense, but it is spread out:
  a 2 h lecture or exercise in most half-days. Only {cal['regular_free_halfdays_mean']:.1f}
  half-days per week are free week after week, at most {cal['regular_free_halfdays_max']}.
  For a slot that has to recur weekly, the effective busy fraction is about
  {cal['busy_equiv_4h']:.2f}, not the {cal['busy_frac_4h_mean']:.2f} the average suggests.
  Two-hour gaps are more common: {cal['regular_free_2h_mean']:.1f} of 20 two-hour slots are
  regularly free.
- **The makerspace course must bring its own slots.** A typical spring course has about
  6 h/week in about 2 regular half-days. A big cohort is then capped even before other
  programmes compete for the room. For example, CTFYS1 with 6 sections of 24, 2 sections in
  the room at once and 2 half-days a week gets 4 section-sessions a week. That is about
  13 h per group over a 5-week project phase.
- **Year 3 (spring): almost no mandatory teaching.** CTFYS3 and CTMAT3 have none, CFATE3
  has about 7 h/week. Students follow electives and the bachelor thesis (SA114X, SA115X,
  SA120X, MF131X), which already have 40–57 scheduled "Projektarbete" occasions each. The
  thesis is a natural vehicle for year-3 makerspace projects; the model treats year 3 like
  years 1–2, which is conservative.
- **Correlation between programmes** of busy half-days is {min(cal['pairwise_corr_mandatory']):.2f}
  to {max(cal['pairwise_corr_mandatory']):.2f}, consistent with the 0.3 assumed.
- **Masters and autumn:** there is no timetable data for these. Master cohorts keep the
  assumed 70% busy with 2 owned half-days, and autumn is assumed to look like spring.

**What it does to the results (coordinated scenario):**

{md_table(tvar_t)}

## Concrete proposals: CTFYS

These three courses are pinned to their real periods and timetable slots in
`data/course_plans.yaml`. All other programme-years stay generic, in the coordinated
scenario.

1. **SK1105 Experimentell fysik** (CTFYS1, P4): replace 1–2 of the 4 three-hour labs with a
   makerspace project, planned at 8 h per group. The labs already run as sessions of about
   24 students on Tue and Wed, mornings and afternoons, in weeks 2–7 (VT26 and VT27). So
   the course owns 4 half-days a week for the whole cohort.
2. **SE1055 Hållfasthetslära** (CTFYS2, P3): a project in place of some exercise sessions,
   8 h. It uses the course's 4 regular half-days in VT27 P3, weeks 3–7. These are
   whole-cohort lecture and exercise slots. **SG1113** (P1) is the alternative, but autumn
   timetables aren't in the data.
3. **Year 3:** replace one of the four labs with a bigger project. Students build a
   measurement device, calibrate it, then go and measure something with it; 12 h in the
   makerspace. I assumed the course is SH1015 Tillämpad modern fysik in P1, the mandatory
   year-3 course with labs. Its slots are unknown, so it gets 2 generic half-days.

Each cell shows mean hours delivered of the hours planned, with the share of simulated
timetables in which every section got everything in brackets. "Others plan" is what
every other programme-year asks for. 16 h is a stress test that over-asks the room.

{md_table(cp_t)}

- **SK1105 is close to an ideal vehicle.** {g(8, v0, 'CTFYS-1', 'mean'):.0f} h fits in
  {g(8, v0, 'CTFYS-1', 'served'):.0%} of timetables when the others keep to 8 h, and still
  {g(12, v0, 'CTFYS-1', 'served'):.0%} when they ask for 12 h. Even 12 h in SK1105 fits
  ({g(8, 'SK1105 at 12 h', 'CTFYS-1', 'served'):.0%}) if the others keep to 8 h. The course
  already works in lab-sized sections and owns 4 half-days, so the makerspace only replaces
  the lab room.
- **SE1055 at 8 h works if the others keep to about 8 h**
  ({g(8, v0, 'CTFYS-2', 'served'):.0%}). It gets tight at 12 h
  ({g(12, v0, 'CTFYS-2', 'served'):.0%}), because its six sections must share four
  fixed half-days in which other programme-years also want the room.
- **The year-3 device project does not fit as a 12 h block in 2 half-days at the end of
  the period** ({g(8, v0, 'CTFYS-3', 'served'):.0%} fully served). It fits when the course
  owns 3 half-days and the project runs through the whole period
  ({g(8, vbest, 'CTFYS-3', 'served'):.0%} with others at 8 h,
  {g(12, vbest, 'CTFYS-3', 'served'):.0%} at 12 h). Only building and calibrating need the
  makerspace; the measuring can happen elsewhere, so 8 h in the room plus open access may
  be enough.
- **All of this needs an allocation rule.** When every programme-year asks for 16 h, even
  SK1105 at 8 h gets everything in only {g(16, v0, 'CTFYS-1', 'served'):.0%} of timetables.
  A fair-share cap of about 8 h per course, plus booked exceptions for courses like these,
  is consistent with the capacity estimates above.

## Section size matters, non-linearly

{md_table(sw)}

Sections of 16–24 let 2–3 sections share the course space at once. A section of 32 runs
alone and leaves about a third of 3203's 50 places unused. Whole-class sessions of 48 are better than 32
but poorer than 24. **Plan sections at ≤24 students, or ≈48.**

## Per programme-year

Mean scheduled hours per group if every course plans {planned} h:

{md_table(per)}

## Open access and machines

{md_table(oa_t)}

- **Staffing binds before space.** Budgeted staffing gives about {o['open_hours']:.0f} open
  hours a week. Covering all documented evening and Saturday hours (17–21, Sat 10–16)
  needs about {oa_i.loc[('documented','matched'),'Staff FTE']} staff FTE. The Education
  deck's extended hours (17–23, weekends 10–23) need about
  {oa_i.loc[('extended','matched'),'Staff FTE']} staff FTE plus
  {oa_i.loc[('extended','matched'),'Student-assistant FTE']} student-assistant FTE, and would
  give about {oa_i.loc[('extended','matched'),'Z (h/group/yr)']:.0f} h per group.
- This assumes App E's evening rule of 1 staff + 1 student assistant per open hour, with
  {fac['staffing']['staff_supervision_share']:.0%} of staff time available for supervision.

{md_table(mach_t)}

- **The single desktop CNC is the first machine bottleneck.** At
  {cnc['utilisation']:.0%} utilisation it allows only about
  {cnc['max_h_per_using_group']:.1f} h for each group that needs it. It is also daytime-only.
  A second bench CNC, as the Plan already lists ("(2)"), would roughly double this.
- **3D printers are not a bottleneck** at {pr['utilisation']:.0%} utilisation for
  {p['machines']['printer_h_per_group']} printer-hours per group.

## Sensitivity

![Sensitivity](results/figures/sensitivity.png)

{md_table(sens_t)}

## Method (first-order)

1. **Fluid bound** (`model/capacity.py`). Supply in section-hours is periods × room-hours
   per period × parallel sections. Room-hours per period are
   {p['calendar']['teaching_weeks_per_period']} weeks × 5 days ×
   {fac['hours']['daytime_bookable_h_per_day']} h × {p['room']['booking_efficiency']} booking
   efficiency × (1 − {p['room']['other_daytime_use']} other use) =
   {meta['room_hours_per_period']:.1f} h. Parallel sections are
   ⌊(50 + 8 + 4) / ({S} + {p['course']['teachers_per_section']})⌋ = {a['parallel_sections']}.
   So supply = 4 × {meta['room_hours_per_period']:.1f} × {a['parallel_sections']} =
   {a['section_hours_supply']:.0f} section-hours, shared by {a['total_sections']} sections,
   which gives **Y_max = {meta['analytic']['Y_max']:.2f} h**.
2. **Timetable simulation** (`model/schedule.py`).
   - Each programme-year is placed in a period: balanced by the school, or chosen at random.
   - It gets a weekly busy mask from its other teaching, with at least
     {scenario_params('coordinated')['timetable']['min_free_blocks']} free blocks guaranteed,
     since the makerspace course is one of its own courses.
   - Its sections are packed greedily into free 3203 slots, at most
     {p['course']['max_sessions_per_week']} sessions per section per week, preferring the
     least-contested slot.
   - This is repeated over many random timetables. The greedy packing is a heuristic: a
     skilled human scheduler might do slightly better, but cannot beat the fluid bound.
   - Check: with no conflicts and every course over-asking, the simulation delivers the
     fluid bound to within 3% (`tests/test_model.py`).
3. **Open access** (`model/open_access.py`).
   - Staffed open hours per week = min(wanted hours, staff hours available, student-assistant
     hours available).
   - Users at once = min(room capacity, users per supervisor × supervisors). Occupancy and
     non-course use are discounted, and the result is divided by persons present per group visit.
   - Machine-hours per year are compared against the demand per group.

## Caveats: replace these first

- **Cohort sizes.** Year-1 and external-master numbers are UHR admission figures, not
  registrations. Year-2/3 retention, COPEN inflow, master show-up rate and the internal flow
  from civilingenjör year 3 into SCI masters are all assumptions (`data/cohorts.yaml`). The
  joint Mathematics master is a placeholder. Registered numbers per programme-year from
  SCI study administration (Ladok) would remove the largest uncertainty.
- **Project weeks and master timetables** now drive the realistic scenarios. Bachelor
  timetables are measured for spring only. Master timetables (TimeEdit) and the autumn
  periods would complete the calibration.
- **Not modelled:**
  - scheduled course sessions in the evening
  - the AlbaNova satellite
  - Alt 2 (377 m²)
  - student-team use of 3203 beyond the {p['room']['other_daytime_use']:.0%} "other use"
  - safety-induction load (small: about 1.5 h per new student, in groups)
  - courses whose project needs more than one programme-year's slot
- **Where the documents disagree, these choices were made:**
  - 3203's 50-person limit is taken as binding for sessions
  - Appendix E's opening hours are the documented case; the Education deck's hours are the extended case
  - only 1 desktop CNC is counted, since only one is budgeted
"""
    (ROOT / "report.md").write_text(text, encoding="utf-8")
    print("wrote report.md")


if __name__ == "__main__":
    main()
