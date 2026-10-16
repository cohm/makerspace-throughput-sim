"""Layer 2: Monte Carlo slot-packing of supervised sessions into room 3203.

Time grid per period: teaching weeks x (5 days x blocks/day) daytime slots.
Each slot offers `parallel_sections` section-places, removed at random with
probability 1 - booking_efficiency x (1 - other_daytime_use).
Each cohort has a weekly busy mask (other teaching), the same every week of the
period, with at least `min_free_blocks` free slots (the course's own timetable slots); sections of a cohort can only meet when the cohort is free.
Each section needs ceil(Y / session_hours) sessions, at most
max_sessions_per_week per week, within the last `project_weeks` weeks of its period.
Packing is a contention-aware greedy heuristic, week by week (see simulate_once);
it gives a lower bound on what a skilled human scheduler could achieve.

Concrete course plans (data/course_plans.yaml, passed as p["course_plans"]) pin a
programme-year's makerspace course to a period, the exact half-days it owns in the
timetable, its project weeks and its own planned hours; other cohorts stay generic.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .capacity import parallel_sections, with_sections


def assign_periods(sections: np.ndarray, p: dict, rng: np.random.Generator,
                   fixed: np.ndarray | None = None) -> np.ndarray:
    """Period per course. `fixed` (period index or -1) pins planned courses; the
    balanced allocation then levels the remaining load around them."""
    P = p["calendar"]["periods"]
    mix = np.asarray(p["timetable"]["period_mix"], float)
    mix = mix / mix.sum()
    fixed = np.full(len(sections), -1) if fixed is None else np.asarray(fixed)
    if p["timetable"]["period_assignment"] == "balanced":
        load = np.zeros(P)
        out = np.empty(len(sections), int)
        for i in np.flatnonzero(fixed >= 0):
            out[i] = fixed[i]
            load[fixed[i]] += sections[i]
        for i in np.argsort(-sections, kind="stable"):
            if fixed[i] >= 0:
                continue
            j = int(np.argmin((load + sections[i]) / mix))
            out[i], load[j] = j, load[j] + sections[i]
        return out
    out = rng.choice(P, size=len(sections), p=mix)
    return np.where(fixed >= 0, fixed, out)


DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def plan_arrays(plans, fac: dict, p: dict, n: int):
    """Turn per-course plan dicts (or None) into arrays used by simulate_once:
    fixed period, own-slot mask (or None), first/last project week index, and the
    number of generic own slots (-1 = use the level default)."""
    L = p["course"]["session_hours"]
    B = fac["hours"]["daytime_bookable_h_per_day"] // L     # slots per day
    W, T = 5 * B, p["calendar"]["teaching_weeks_per_period"]
    w0 = T - p["timetable"]["project_weeks"]
    fixed, masks, own_n = np.full(n, -1), [None] * n, np.full(n, -1)
    first, last = np.full(n, w0), np.full(n, T - 1)
    for i, pl in enumerate(plans if plans is not None else [None] * n):
        if not pl:
            continue
        fixed[i] = pl["period"] - 1
        if pl.get("own_blocks"):
            mk = np.zeros(W, bool)
            for b in pl["own_blocks"]:
                d, half = b.split()
                start = DAYS.index(d) * B + (0 if half == "am" else B // 2)
                mk[start:start + max(1, B // 2)] = True
            masks[i] = mk
        elif pl.get("own_halfdays"):            # count known, positions not: generic slots
            own_n[i] = pl["own_halfdays"] * max(1, B // 2)
        if pl.get("weeks"):
            first[i] = min(pl["weeks"][0], T) - 1
            last[i] = min(pl["weeks"][1], T) - 1
    return fixed, masks, first, last, own_n


def level_params(levels, p: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per-course busy_fraction and min_free_blocks, with timetable.by_level overrides
    (e.g. bachelor cohorts calibrated from real timetables, masters assumed)."""
    t = p["timetable"]
    by = t.get("by_level") or {}
    f = np.array([by.get(lv, {}).get("busy_fraction", t["busy_fraction"]) for lv in levels], float)
    mf = np.array([by.get(lv, {}).get("min_free_blocks", t["min_free_blocks"]) for lv in levels], int)
    return f, mf


def simulate_once(sections: np.ndarray, fac: dict, p: dict, sessions_needed,
                  rng: np.random.Generator, levels=None, plans=None) -> tuple[np.ndarray, np.ndarray]:
    """Return (ok, delivered): did each course get all sessions for all its sections,
    and the mean number of sessions its sections actually got.
    `sessions_needed` is a scalar or one value per course; `plans` an optional list of
    course-plan dicts (or None) per course."""
    c, t, room = p["course"], p["timetable"], p["room"]
    L = c["session_hours"]
    W = 5 * (fac["hours"]["daytime_bookable_h_per_day"] // L)
    T = p["calendar"]["teaching_weeks_per_period"]
    k = parallel_sections(fac, p)
    p_avail = room["booking_efficiency"] * (1 - room["other_daytime_use"])
    rho = t["busy_correlation"]
    f_all, mf_all = level_params(levels if levels is not None else [None] * len(sections), p)

    m_all = np.broadcast_to(np.asarray(sessions_needed), (len(sections),))
    fixed, own_masks, first, last, own_n = plan_arrays(plans, fac, p, len(sections))
    mf_all = np.where(own_n >= 0, own_n, mf_all)
    period = assign_periods(sections, p, rng, fixed)
    ok = np.zeros(len(sections), bool)
    delivered = np.zeros(len(sections))
    for per in range(p["calendar"]["periods"]):
        idx = np.flatnonzero(period == per)
        if idx.size == 0:
            continue
        # Busy if a uniform draw < the cohort's busy fraction; with probability rho the
        # draw is shared by all cohorts in the period (correlated timetables).
        f = f_all[idx][:, None]
        base = rng.random(W)
        own = rng.random((idx.size, W))
        shared = rng.random((idx.size, W)) < rho
        free = ~(np.where(shared, base, own) < f)
        # The makerspace course is itself one of the cohort's courses, so it owns
        # at least `min_free_blocks` slots per week: free random busy slots up to that.
        for r in range(idx.size):
            short = mf_all[idx[r]] - free[r].sum()
            if short > 0:
                free[r, rng.choice(np.flatnonzero(~free[r]), short, replace=False)] = True
            if own_masks[idx[r]] is not None:      # planned course: exactly its own slots
                free[r] = own_masks[idx[r]]
        cap = np.where(rng.random((T, W)) < p_avail, k, 0)

        sec_coh = np.repeat(np.arange(idx.size), sections[idx])
        need = np.repeat(m_all[idx], sections[idx]).astype(int)
        sec_first, sec_last = np.repeat(first[idx], sections[idx]), np.repeat(last[idx], sections[idx])
        for w in range(int(sec_first.min()), int(sec_last.max()) + 1):
            used = np.zeros((sec_coh.size, W), bool)
            # sections outside their project weeks take no slot this week
            used[(w < sec_first) | (w > sec_last)] = True
            for _ in range(c["max_sessions_per_week"]):
                # Contention-aware greedy: most constrained sections pick first
                # (fewest usable slots per session still needed), each taking the
                # usable slot with the least outstanding demand per free place.
                usable = free[sec_coh] & (cap[w] > 0) & ~used
                slack = usable.sum(1) / np.maximum(need, 1)
                order = rng.permutation(sec_coh.size)
                order = order[np.argsort(slack[order], kind="stable")]
                for s in order:
                    if need[s] == 0:
                        continue
                    cand = free[sec_coh[s]] & (cap[w] > 0) & ~used[s]
                    if not cand.any():
                        continue
                    pressure = ((need > 0)[:, None] & free[sec_coh] & ~used).sum(0)
                    score = np.where(cand, pressure / np.maximum(cap[w], 1) + rng.random(W) * 1e-3, np.inf)
                    j = int(np.argmin(score))
                    cap[w, j] -= 1
                    need[s] -= 1
                    used[s, j] = True
        done = np.ones(idx.size, bool)
        np.logical_and.at(done, sec_coh, need == 0)
        ok[idx] = done
        got = np.zeros(idx.size)
        np.add.at(got, sec_coh, np.repeat(m_all[idx], sections[idx]) - need)
        delivered[idx] = got / sections[idx]
    return ok, delivered


def _course_sections(cohorts: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, np.ndarray, int]:
    """Each cohort runs `courses_per_cohort` makerspace courses; each course is placed
    independently (own period, own timetable mask). Returns (df, sections per course, X)."""
    df = with_sections(cohorts, p)
    X = int(p["course"]["courses_per_cohort"])
    return df, np.repeat(df["sections"].to_numpy(), X), X


def _levels(df: pd.DataFrame, X: int) -> np.ndarray:
    return np.repeat(df["level"].to_numpy(), X)


def _plans(df: pd.DataFrame, p: dict, X: int) -> list:
    """Course plan per course instance; only the first of a cohort's X courses is planned."""
    plans = p.get("course_plans") or {}
    return [plans.get(c) if x == 0 else None for c in df["cohort"] for x in range(X)]


def success_rates(cohorts: pd.DataFrame, fac: dict, p: dict, sessions_needed: int,
                  draws: int | None = None, seed: int | None = None) -> np.ndarray:
    """Per-cohort probability that all its courses get all sessions, over `draws` random timetables."""
    df, sections, X = _course_sections(cohorts, p)
    draws = draws or p["sim"]["draws"]
    rng = np.random.default_rng(p["sim"]["seed"] if seed is None else seed)
    hits = np.zeros(len(df))
    for _ in range(draws):
        hits += simulate_once(sections, fac, p, sessions_needed, rng, _levels(df, X),
                              _plans(df, p, X))[0].reshape(-1, X).all(1)
    return hits / draws


def delivered_hours(cohorts: pd.DataFrame, fac: dict, p: dict, requested_hours: float,
                    draws: int | None = None, seed: int | None = None) -> dict:
    """If every course plans `requested_hours` per group, what do groups actually get
    (per course)? Returns mean over all students, and the 5th percentile over (cohort, draw)."""
    df, sections, X = _course_sections(cohorts, p)
    L = p["course"]["session_hours"]
    plans = _plans(df, p, X)
    # planned courses ask for their own hours; everyone else for `requested_hours`
    m = np.array([math.ceil((pl.get("planned_hours", requested_hours) if pl else requested_hours) / L)
                  for pl in plans])
    draws = draws or p["sim"]["draws"]
    rng = np.random.default_rng(p["sim"]["seed"] if seed is None else seed)
    runs = [simulate_once(sections, fac, p, m, rng, _levels(df, X), plans) for _ in range(draws)]
    got_h = np.array([r[1].reshape(-1, X).mean(1) for r in runs]) * L
    served = np.array([r[0].reshape(-1, X).all(1) for r in runs]).mean(0)
    w = df["students"].to_numpy()
    return {"requested": int(math.ceil(requested_hours / L) * L),
            "requested_per_cohort": m.reshape(-1, X)[:, 0] * L,
            "mean": float((got_h * w).sum() / (w.sum() * draws)),
            "p5": float(np.percentile(got_h, 5)),
            "per_cohort_mean": got_h.mean(0),
            "per_cohort_served": served}


def feasible_hours(cohorts: pd.DataFrame, fac: dict, p: dict, draws: int | None = None) -> dict:
    """Largest Y (multiple of session_hours) for which the mean share of cohorts that
    get all their sessions is >= success_threshold. Common random numbers across Y."""
    c = p["course"]
    L = c["session_hours"]
    m_cap = p["timetable"]["project_weeks"] * c["max_sessions_per_week"]
    thr = p["sim"]["success_threshold"]
    best_m, best_rates, curve = 0, None, []
    for m in range(1, m_cap + 1):
        rates = success_rates(cohorts, fac, p, m, draws)
        curve.append((m * L, float(rates.mean())))
        if rates.mean() < thr:
            break
        best_m, best_rates = m, rates
    if best_rates is None:
        best_rates = np.zeros(len(cohorts))
    return {"Y": best_m * L, "sessions": best_m, "cohort_success": best_rates, "curve": curve}
