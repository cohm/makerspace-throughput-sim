# SCI Makerspace throughput model

This is a first-order estimate of how much makerspace time each SCI programme-year can get.
The goal it tests is one makerspace project course per year for every SCI programme-year:
CTFYS, CTMAT and CFATE years 1–3, COPEN, and 11 masters × 2 years. The facility is the
SCI MakerSpace at Teknikringen 8, Alternative 1. The planning documents the inputs cite
(implementation plan, appendices, presentations) are internal to the project and not in this
repository; `data/facility.yaml` gives the document and page for every number taken from them.

**Results:** `report.md`, generated from the files in `results/`.

**Interactive version:** `web/index.html`. It is self-contained, built by `run.py` from
`web/template.html`, `web/model.js` and `web/sim.js`. `run.py` also copies it to
`docs/index.html`, which GitHub Pages serves (Settings → Pages → branch `main`, folder `/docs`).

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install numpy pandas pyyaml matplotlib pytest
export MPLCONFIGDIR="${TMPDIR:-/tmp}/mpl"   # only needed if ~/.matplotlib is not writable
.venv/bin/python -I tools/schedule_from_review.py   # only when the timetable sources change
.venv/bin/python -I tools/schedule_analysis.py
.venv/bin/python run.py          # about 45 s. Writes results/, figures, web/index.html
.venv/bin/python report.py       # writes report.md
.venv/bin/python -m pytest -q tests
node tests/parity.cjs            # checks the JS model against the Python results
node tests/sim.test.cjs          # checks the course-simulation scheduler's rules
```

`run.py --quick` uses fewer Monte Carlo draws, for trying out changes.

## Layout

| Path | What |
|---|---|
| `data/facility.yaml` | Rooms, person limits, machines, opening hours and staffing, each with its source document and page |
| `data/cohorts.yaml` | UHR admission numbers (HT24–HT26) and the assumptions that turn them into cohort sizes |
| `data/scenarios.yaml` | Model parameters (`base`) and the four scenarios: optimal, coordinated, uncoordinated, concentrated |
| `data/course_plans.yaml` | Concrete makerspace course proposals pinned to real periods, slots and hours (currently CTFYS: SK1105, SE1055, year-3 device project) |
| `model/inputs.py` | Loads YAML, merges scenarios, derives programme-year cohorts |
| `model/capacity.py` | Layer 1: analytic (fluid) upper bound |
| `model/schedule.py` | Layer 2: Monte Carlo timetable and slot-packing simulation |
| `model/open_access.py` | Layer 3: staffed open-access hours and machine capacity |
| `run.py` / `report.py` | Run everything, then write the report |
| `tools/schedule_from_review.py` | Extracts the VT26/VT27 bachelor timetables from the kth-schedule-review examples into `data/schedule_events.csv`. Sources are cached in `data/schedule_review/`; `--fetch` re-downloads them |
| `tools/schedule_analysis.py` | Measures busy half-days and regularly free slots, writes `results/schedule_*.csv`, the heatmap figure and `results/schedule_calibration.json`, which is the basis for the bachelor `by_level` values in `scenarios.yaml` |
| `model/figstyle.py`, `fonts/` | KTH graphical profile for figures (colours from `cohm/kth-doc-templates`); static Figtree Regular/SemiBold (SIL OFL, `fonts/OFL.txt`) because matplotlib cannot use the variable Figtree font's weights |
| `tools/fetch_kth_courses.py` | Fetches the 2026 programme syllabi (course lists per year) and each course's examination modules from kth.se into `data/kth_courses/`; used to pick plausible makerspace courses (LAB/PRO modules) for the simulation |
| `data/sim_courses.yaml` | The "Course simulation 2027/28" tab: one real course per programme-year with a MOCK EXAMPLE makerspace activity, its takers (programme-years and shares), group size, with period, project weeks, hours, owned half-days, illustration motif; plus calendar and slot rules |
| `web/sim.js` | Course simulation: seeded scheduler (`SimCore`, tested by `tests/sim.test.cjs`) and the tab's UI (jigsaw illustrations, board, stepping) |
| `web/model.js` | JS port of the three layers, used by the page and by `tests/parity.cjs` |

## Changing assumptions

Edit the YAML files and re-run. Every number is either tagged with a source (`src:`) or
sits under `assumptions:`. The assumptions most worth replacing with real data:

- **Registered cohort sizes per programme-year**, from SCI study administration (Ladok).
  These would replace the assumed retention, master show-up rate, and internal flow into
  the masters.
- **Master and autumn timetables.** Bachelor spring timetables are already measured from
  the schedule reviews. Master programmes and P1–P2 still use assumed values.
- **Which periods candidate courses run in.** This would replace the random or balanced
  period assignment.
