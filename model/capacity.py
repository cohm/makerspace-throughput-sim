"""Layer 1: analytic (fluid) upper bound on scheduled project hours per group."""
from __future__ import annotations

import math

import pandas as pd

from .inputs import sections_for


def session_person_cap(fac: dict, p: dict) -> int:
    cap = fac["rooms"]["r3203"]["max_persons"]
    if p["room"]["side_rooms_in_sessions"]:
        cap += fac["rooms"]["r3202"]["max_persons"] + fac["rooms"]["studio3d"]["max_persons"]
    return cap


def parallel_sections(fac: dict, p: dict) -> int:
    """How many sections (students + teachers) fit in the course space at once."""
    c = p["course"]
    return session_person_cap(fac, p) // (c["section_size"] + c["teachers_per_section"])


def room_hours_per_period(fac: dict, p: dict) -> float:
    """Bookable 3203 daytime hours in one period, after efficiency and other use."""
    weeks = p["calendar"]["teaching_weeks_per_period"]
    h = weeks * 5 * fac["hours"]["daytime_bookable_h_per_day"]
    return h * p["room"]["booking_efficiency"] * (1 - p["room"]["other_daytime_use"])


def with_sections(cohorts: pd.DataFrame, p: dict) -> pd.DataFrame:
    df = cohorts.copy()
    S, g = p["course"]["section_size"], p["course"]["group_size"]
    df["sections"] = [sections_for(n, S) for n in df["students"]]
    df["groups"] = [math.ceil(n / g) for n in df["students"]]
    return df


def analytic(cohorts: pd.DataFrame, fac: dict, p: dict) -> dict:
    """Fluid bound: all section-hours of supply shared evenly, perfect spreading.

    Y_max = (periods x room_h_per_period x parallel_sections) / (X x sum(sections))
    also capped by the per-section limit weeks x max_sessions_per_week x session_hours.
    """
    df = with_sections(cohorts, p)
    k = parallel_sections(fac, p)
    X = p["course"]["courses_per_cohort"]
    supply = p["calendar"]["periods"] * room_hours_per_period(fac, p) * k
    demand_units = X * df["sections"].sum()
    y_room = supply / demand_units if k > 0 else 0.0
    c = p["course"]
    y_cap = p["timetable"]["project_weeks"] * c["max_sessions_per_week"] * c["session_hours"]
    return {
        "parallel_sections": k,
        "room_hours_per_year": p["calendar"]["periods"] * room_hours_per_period(fac, p),
        "section_hours_supply": supply,
        "total_sections": int(df["sections"].sum()),
        "total_groups": int(df["groups"].sum()),
        "total_students": int(df["students"].sum()),
        "Y_room_bound": y_room,
        "Y_section_cap": y_cap,
        "Y_max": min(y_room, y_cap),
    }


def courses_for_target(y_max: float, target_hours: float) -> float:
    """Inverse question: how many courses per cohort at `target_hours` each."""
    return y_max / target_hours if target_hours > 0 else float("inf")
