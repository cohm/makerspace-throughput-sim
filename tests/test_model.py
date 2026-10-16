"""Sanity tests: analytic hand calculations, sim-vs-fluid agreement, monotonicity."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.capacity import analytic, room_hours_per_period  # noqa: E402
from model.inputs import build_cohorts, load_yaml, scenario_params  # noqa: E402
from model.open_access import open_access  # noqa: E402
from model.schedule import delivered_hours, feasible_hours  # noqa: E402

FAC = load_yaml("facility.yaml")
CO = build_cohorts()


def test_cohort_year1_is_uhr_mean():
    c = CO.set_index("cohort")["students"]
    assert c["CTFYS-1"] == round((140 + 143) / 2)
    assert c["CTMAT-1"] == 75
    assert len(CO) == 10 + 22


def test_analytic_hand_calculation():
    p = scenario_params("optimal")
    # 7 weeks x 5 days x 8 h x 0.85 booking efficiency x (1 - 0.10 other use)
    assert room_hours_per_period(FAC, p) == pytest.approx(7 * 5 * 8 * 0.85 * 0.9)
    a = analytic(CO, FAC, p)
    assert a["parallel_sections"] == 2                       # (50+8+4) // (24+1)
    assert a["Y_max"] == pytest.approx(4 * 214.2 * 2 / a["total_sections"])


def test_open_access_hand_calculation():
    p = scenario_params("optimal")
    o = open_access(CO, FAC, p)
    staffed = min(26, 1.3 * 40 * 0.75 * 0.5, 1.0 * 40 * 0.75)   # = 19.5, staff-bound
    expect = staffed * 34 * min(54, 20 * 2) * 0.6 * 0.7 / (o["groups"] * 2)
    assert o["open_hours"] == pytest.approx(19.5)
    assert o["Z"] == pytest.approx(expect)


def test_sim_saturates_at_fluid_bound():
    """With no conflicts, if every course asks for more than fits, the simulation
    delivers on average what the fluid bound says (within 3%)."""
    p = scenario_params("optimal")
    a = analytic(CO, FAC, p)
    d = delivered_hours(CO, FAC, p, requested_hours=24, draws=20)
    assert d["mean"] == pytest.approx(a["Y_max"], rel=0.03)


def test_sim_feasible_below_bound():
    p = scenario_params("optimal")
    a = analytic(CO, FAC, p)
    y = feasible_hours(CO, FAC, p, draws=20)["Y"]
    assert a["Y_max"] - p["course"]["session_hours"] <= y <= a["Y_max"]


def test_monotone_in_busy_fraction_and_concentration():
    lo = scenario_params("coordinated", {"timetable": {"busy_fraction": 0.4}})
    hi = scenario_params("coordinated", {"timetable": {"busy_fraction": 0.8}})
    d_lo = delivered_hours(CO, FAC, lo, 16, draws=20)["mean"]
    d_hi = delivered_hours(CO, FAC, hi, 16, draws=20)["mean"]
    assert d_hi < d_lo
    unc = delivered_hours(CO, FAC, scenario_params("uncoordinated"), 16, draws=30)["mean"]
    con = delivered_hours(CO, FAC, scenario_params("concentrated"), 16, draws=30)["mean"]
    assert con < unc


def test_two_courses_halve_fluid_capacity():
    p1 = scenario_params("optimal")
    p2 = scenario_params("optimal", {"course": {"courses_per_cohort": 2}})
    a1, a2 = analytic(CO, FAC, p1), analytic(CO, FAC, p2)
    assert a2["Y_max"] == pytest.approx(a1["Y_max"] / 2)
    d2 = delivered_hours(CO, FAC, p2, requested_hours=16, draws=15)
    assert d2["mean"] == pytest.approx(a2["Y_max"], rel=0.05)


def test_bachelor_override_only_affects_bachelor_cohorts():
    from model.schedule import level_params
    p = scenario_params("coordinated")
    f, mf = level_params(["bachelor", "master"], p)
    assert f[0] == pytest.approx(0.93) and f[1] == pytest.approx(0.7)
    more = scenario_params("coordinated", {"timetable": {"by_level": {"bachelor": {"min_free_blocks": 4}}}})
    base = delivered_hours(CO, FAC, p, 16, draws=20)["per_cohort_mean"]
    hi = delivered_hours(CO, FAC, more, 16, draws=20)["per_cohort_mean"]
    bach = (CO.level == "bachelor").to_numpy()
    assert hi[bach].mean() > base[bach].mean()


def test_course_plan_pins_period_slots_and_hours():
    from model.schedule import plan_arrays
    p = scenario_params("coordinated")
    pl = {"period": 4, "own_blocks": ["Tue am", "Wed pm"], "weeks": [2, 7], "planned_hours": 8}
    fixed, masks, first, last, own_n = plan_arrays([pl, None], FAC, p, 2)
    assert fixed.tolist() == [3, -1]
    assert masks[0].tolist() == [False, False, True, False, False, True, False, False, False, False]
    assert (first[0], last[0]) == (1, 6) and masks[1] is None and own_n.tolist() == [-1, -1]
    # Alone in the building (others ask for 4 h), a 4-half-day lab-style plan gets its 8 h.
    q = scenario_params("coordinated")
    q["course_plans"] = {"CTFYS-1": {"period": 4, "own_blocks": ["Tue am", "Tue pm", "Wed am", "Wed pm"],
                                     "weeks": [2, 7], "planned_hours": 8}}
    d = delivered_hours(CO, FAC, q, 4, draws=20)
    i = CO.index[CO.cohort == "CTFYS-1"][0]
    assert d["requested_per_cohort"][i] == 8 and d["per_cohort_mean"][i] == pytest.approx(8, abs=0.3)
