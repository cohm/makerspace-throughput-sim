"""Fetch SCI programme curricula and course examination modules from kth.se.

Sources (public pages; the course list is embedded as JSON in each curriculum page):
  https://www.kth.se/student/kurser/program/<PROG>/<TERM>/arskurs<N>?l=en
  https://www.kth.se/student/kurser/kurs/<CODE>?l=en      (Examination section)

Writes data/kth_courses/curricula.csv (programme, track, year, course, status, periods)
and data/kth_courses/course_modules.csv (course, module code, name, credits), used to pick
plausible makerspace courses (with LAB/PRO-type modules) for the simulation's mock examples.
Status codes in the curricula: O mandatory, VV conditionally elective, V elective, R recommended.

Usage: .venv/bin/python tools/fetch_kth_courses.py [--term 20262]
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "kth_courses"
PROGRAMMES = {"CTFYS": 3, "CTMAT": 3, "CFATE": 3, "COPEN": 1,
              "TTFYM": 2, "TAEEM": 2, "TFORM": 2, "TMRSM": 2, "TTEMM": 2, "TTMAM": 2,
              "TNEEM": 2, "TBDVM": 2, "TMAKM": 2}
MODULE = re.compile(r"\b([A-ZÅÄÖ]{3}\d) - ([^,\n]+), ([\d.]+) (?:credits|hp)")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "sci-makerspace-capacity-model (KTH internal planning)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def curriculum(prog: str, term: str, year: int):
    h = get(f"https://www.kth.se/student/kurser/program/{prog}/{term}/arskurs{year}?l=en")
    m = re.search(r'window\.__compressedApplicationStore__ = "([^"]*)"', h)
    if not m:
        return None, []
    d = json.loads(urllib.parse.unquote(m.group(1)))
    title = html.unescape(re.search(r"<title>(.*?)</title>", h, re.S).group(1)).split(",")[0]
    rows = []
    for cu in d.get("curriculums", []):
        spec = cu.get("programmeSpecialisation") or {}
        track = spec.get("code", "") if isinstance(spec, dict) else ""
        tname = spec.get("title", "") if isinstance(spec, dict) else ""
        for sy in cu.get("studyYears", []):
            if sy.get("yearNumber") != year:
                continue
            for c in sy.get("courses", []):
                periods = sorted({p["Lasperiodskod"] for t in c.get("Tillfallesperioder", [])
                                  for p in t.get("Lasperiodsfordelning", [])})
                rows.append({"programme": prog, "track": track, "track_name": tname, "year": year,
                             "course": c["kod"], "name": c["benamning"], "status": c.get("Valvillkor", ""),
                             "credits": (c.get("omfattning") or {}).get("number", ""), "periods": " ".join(periods)})
    return title, rows


def modules(code: str):
    h = get(f"https://www.kth.se/student/kurser/kurs/{code}?l=en")
    t = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)))
    i = t.find("Examination\n")
    seg = t[i:i + 3000] if i >= 0 else t
    return [{"course": code, "module": a, "module_name": b.strip(), "credits": c} for a, b, c in MODULE.findall(seg)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--term", default="20262", help="intake term of the programme syllabus (20262 = HT2026)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, titles = [], {}
    for prog, years in PROGRAMMES.items():
        for y in range(1, years + 1):
            title, r = curriculum(prog, a.term, y)
            titles[prog] = title
            rows += r
            print(f"{prog} year {y}: {len(r)} course rows  ({title})")
            time.sleep(0.4)
    with open(OUT / "curricula.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    (OUT / "programmes.json").write_text(json.dumps(titles, indent=1, ensure_ascii=False), encoding="utf-8")

    codes = sorted({r["course"] for r in rows if r["status"] in ("O", "VV")})
    mods = []
    for i, code in enumerate(codes):
        try:
            mods += modules(code) or [{"course": code, "module": "", "module_name": "(none found)", "credits": ""}]
        except Exception as e:  # noqa: BLE001 - keep going, record the failure
            mods.append({"course": code, "module": "", "module_name": f"(fetch failed: {e})", "credits": ""})
        if i % 25 == 0:
            print(f"modules {i}/{len(codes)}")
        time.sleep(0.3)
    with open(OUT / "course_modules.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["course", "module", "module_name", "credits"])
        w.writeheader(); w.writerows(mods)
    print(f"wrote {len(rows)} curriculum rows, {len(mods)} module rows for {len(codes)} courses")


if __name__ == "__main__":
    main()
