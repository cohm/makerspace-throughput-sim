"""Layer 3: open (self-booked) access and machine capacity."""
from __future__ import annotations

from .capacity import with_sections


def staffed_open_hours_per_week(fac: dict, p: dict) -> dict:
    st = fac["staffing"]
    hv = fac["hours"]["open_access_variants"][p["open_access"]["hours_variant"]]
    wanted = 5 * hv["evening_h_per_weekday"] + hv["weekend_h_per_week"]
    staff_h = st["staff_fte"] * st["fte_hours_per_week"] * st["availability"] * st["staff_supervision_share"]
    sa_h = st["student_assistant_fte"] * st["fte_hours_per_week"] * st["availability"]
    by_staff = staff_h / st["staff_per_open_hour"] if st["staff_per_open_hour"] else float("inf")
    by_sa = sa_h / st["sa_per_open_hour"] if st["sa_per_open_hour"] else float("inf")
    hours = min(wanted, by_staff, by_sa)
    binding = "opening hours" if hours == wanted else ("staff" if by_staff <= by_sa else "student assistants")
    return {"wanted": wanted, "by_staff": by_staff, "by_sa": by_sa, "hours": hours, "binding": binding}


def open_access(cohorts, fac: dict, p: dict) -> dict:
    """Z = self-booked hours per group per year (evenings/weekends)."""
    df = with_sections(cohorts, p)
    oa, st = p["open_access"], fac["staffing"]
    groups = df["groups"].sum() * p["course"]["courses_per_cohort"]
    hrs = staffed_open_hours_per_week(fac, p)
    room_cap = sum(fac["rooms"][r]["max_persons"] for r in oa["rooms"])
    supervisors = st["staff_per_open_hour"] + st["sa_per_open_hour"]
    simultaneous = min(room_cap, st["max_users_per_supervisor"] * supervisors)
    person_h = (hrs["hours"] * p["calendar"]["open_weeks_per_year"] * simultaneous
                * oa["occupancy"] * (1 - oa["noncourse_share"]))
    Z = person_h / (groups * oa["persons_present_per_group"])
    return {**{f"open_{k}": v for k, v in hrs.items()},
            "simultaneous_users": simultaneous, "person_hours_course": person_h,
            "groups": int(groups), "Z": Z}


def machines(cohorts, fac: dict, p: dict) -> list[dict]:
    """Machine-hour supply vs. demand per year, and max hours per using group."""
    df = with_sections(cohorts, p)
    m, wk = p["machines"], p["calendar"]["open_weeks_per_year"]
    groups = df["groups"].sum() * p["course"]["courses_per_cohort"]
    spec = [
        ("3D printers", fac["machines"]["fdm_printers"]["count"], m["printer_hours_per_week"],
         m["printer_uptime"], 1.0, m["printer_h_per_group"]),
        ("CNC", fac["machines"]["cnc"]["count"], m["cnc_hours_per_week"],
         m["cnc_uptime"], m["cnc_share_of_groups"], m["cnc_h_per_group"]),
        ("Metal machines", fac["machines"]["metal_machines"]["count"], m["metal_hours_per_week"],
         m["metal_uptime"], m["metal_share_of_groups"], m["metal_h_per_group"]),
    ]
    out = []
    for name, n, hpw, up, share, hpg in spec:
        supply = n * hpw * up * wk
        demand = groups * share * hpg
        out.append({"resource": name, "supply_h": supply, "demand_h": demand,
                    "utilisation": demand / supply, "max_h_per_using_group": supply / (groups * share)})
    return out
