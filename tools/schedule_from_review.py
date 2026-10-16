"""Extract bachelor timetables (VT26 and VT27 draft) from the kth-schedule-review examples.

Sources (copied into data/schedule_review/, re-fetch with --fetch):
  https://cohm.github.io/kth-schedule-review/docs/examples/<PROG>_VT27/<PROG>_VT27_review.html
  https://cohm.github.io/kth-schedule-review/docs/examples/<PROG>_VT27/issues.json
  https://raw.githubusercontent.com/cohm/ProgramVisualization/main/src/data/cohorts/<PROG>-HT20xx.json

The review HTML lists every teaching occasion per course (start time only) for VT26
and VT27, in a per-course "sequence" block tagged with the programme year(s). End
times come from the `cite` strings in issues.json ("CTFYS1_VT27_remiss.xlsx:7
2027-01-12 10:00-12:00 Föreläsning"); where no cite exists the course's most common
duration for that activity type is used, else 2 h. Course category (mandatory /
conditionally elective / recommended) comes from the study plan of the cohort that
is in that year during that term.

Writes data/schedule_events.csv (one row per occasion and programme year).
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "schedule_review"
PROGS = ["CTFYS", "CTMAT", "CFATE", "COPEN"]
EXAM_TYPES = {"Tentamen", "Omtentamen", "Omtenta"}
# Spring term: programme year y is the cohort that started HT(term_year - y)
TERM_YEAR = {"VT26": 2026, "VT27": 2027}
EXAMPLES = "https://cohm.github.io/kth-schedule-review/docs/examples"
PLANS = "https://raw.githubusercontent.com/cohm/ProgramVisualization/main/src/data/cohorts"


def fetch():
    SRC.mkdir(parents=True, exist_ok=True)
    for p in PROGS:
        urllib.request.urlretrieve(f"{EXAMPLES}/{p}_VT27/{p}_VT27_review.html", SRC / f"{p}_review.html")
        urllib.request.urlretrieve(f"{EXAMPLES}/{p}_VT27/issues.json", SRC / f"{p}_issues.json")
        for y in range(2023, 2027):
            urllib.request.urlretrieve(f"{PLANS}/{p}-HT{y}.json", SRC / f"plan_{p}-HT{y}.json")


def load_plan(prog: str, cohort_year: int) -> dict:
    f = SRC / f"plan_{prog}-HT{cohort_year}.json"
    if not f.exists():
        return {}
    return {c["code"]: c.get("category", "?") for c in json.loads(f.read_text()) if "code" in c}


def mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


ACT = re.compile(r"data-en-title='([^']*)'")
TITLE = re.compile(r"^(\S+) [^\n]*\n(.+?) (?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{4}-\d\d-\d\d) (\d\d:\d\d) \(([^)]*)\)")
CITE = re.compile(r"(\w+?)_(VT\d\d)_\w+\.xlsx:\d+ (\d{4}-\d\d-\d\d) (\d\d:\d\d)-(\d\d:\d\d) ([^\"\\]+)")


def parse_programme(prog: str) -> pd.DataFrame:
    page = (SRC / f"{prog}_review.html").read_text(encoding="utf-8")
    issues = json.loads((SRC / f"{prog}_issues.json").read_text(encoding="utf-8"))
    cal = issues["calendar"]
    exam_days = [(a, b) for _, a, b in cal["exam_windows"]]

    # Durations from cites: exact key, then (course-less) fallback by type
    ends = {}
    for label, term, date, start, end, typ in CITE.findall(json.dumps(issues, ensure_ascii=False)):
        ends[(label, term, date, start, typ.strip())] = end

    rows = []
    blocks = re.split(r"<details class='course' ", page)[1:]
    for b in blocks:
        years = re.match(r"data-course-years='([^']*)'", b)
        if not years:
            continue
        years = years.group(1).split()
        seq = re.search(r"<div class='seq'>(.*?)</div></div>", b, re.S)
        if not seq:
            continue
        for row in re.findall(r"<div class='seqrow'><span class='seqlbl'>(VT\d\d)</span>(.*?)</div>", seq.group(0) + "</div>", re.S):
            term, acts = row
            for t in ACT.findall(acts):
                m = TITLE.match(htmllib.unescape(t))
                if not m:
                    continue
                code, typ, date, start, week = m.groups()
                for label in years:
                    rows.append({"programme": prog, "label": label, "year": int(label[-1]), "term": term,
                                 "course": code, "type": typ, "date": date, "start": start, "week": week})
    df = pd.DataFrame(rows).drop_duplicates()

    # Durations
    def end_for(r):
        return ends.get((r.label, r.term, r.date, r.start, r.type))
    df["end"] = [end_for(r) for r in df.itertuples()]
    df["dur_src"] = df["end"].notna().map({True: "cite", False: "assumed"})
    dur = {k: mins(e) - mins(s) for k, e, s in
           ((k, v, k[3]) for k, v in ends.items())}
    typical = defaultdict(Counter)
    for r in df[df.end.notna()].itertuples():
        typical[(r.course, r.type)][mins(r.end) - mins(r.start)] += 1
    def fill(r):
        if isinstance(r.end, str):
            return r.end
        c = typical.get((r.course, r.type))
        d = c.most_common(1)[0][0] if c else 120
        e = mins(r.start) + d
        return f"{e // 60:02d}:{e % 60:02d}"
    df["end"] = [fill(r) for r in df.itertuples()]

    # Category from the study plan of the cohort in that year during that term
    cats = []
    for r in df.itertuples():
        plan = load_plan(prog, TERM_YEAR[r.term] - r.year)
        cats.append(plan.get(r.course, "not in plan"))
    df["category"] = cats

    d = pd.to_datetime(df["date"])
    df["weekday"] = d.dt.weekday
    df["period"] = df["week"].str.extract(r"^(P\d)")[0]
    in_exam = pd.Series(False, index=df.index)
    for a, b in exam_days:      # VT27 windows; shift one year back for VT26
        for term, shift in (("VT27", 0), ("VT26", 1)):
            lo = pd.Timestamp(a) - pd.DateOffset(years=shift)
            hi = pd.Timestamp(b) - pd.DateOffset(years=shift)
            in_exam |= (df.term == term) & (d >= lo) & (d <= hi)
    df["teaching_week"] = df["week"].str.match(r"^P\d w\d+$") & ~in_exam & ~df.type.isin(EXAM_TYPES)
    df["week_no"] = df["week"].str.extract(r"w(\d+)$")[0].astype(float)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="re-download sources into data/schedule_review/")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    df = pd.concat([parse_programme(p) for p in PROGS], ignore_index=True)
    out = ROOT / "data" / "schedule_events.csv"
    df.to_csv(out, index=False)
    print(f"wrote {out}: {len(df)} occasion-rows")
    print(df.groupby(["term", "label"]).size().unstack(0).to_string())
    print(df.dur_src.value_counts().to_string())
    print(df.groupby("category").size().to_string())


if __name__ == "__main__":
    main()
