"""Load YAML inputs, merge scenario overrides, and derive cohort sizes."""
from __future__ import annotations

import copy
import math
from pathlib import Path

import pandas as pd
import yaml

DATA = Path(__file__).resolve().parent.parent / "data"


def load_yaml(name: str) -> dict:
    with open(DATA / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def scenario_params(name: str, extra: dict | None = None) -> dict:
    """Base parameters with the named scenario's overrides (and optional extra overrides)."""
    cfg = load_yaml("scenarios.yaml")
    sc = cfg["scenarios"][name]
    p = deep_merge(cfg["base"], {k: v for k, v in sc.items() if k not in ("label", "description")})
    p = deep_merge(p, extra or {})
    p["name"], p["label"], p["description"] = name, sc["label"], sc["description"]
    return p


def scenario_names() -> list[str]:
    return list(load_yaml("scenarios.yaml")["scenarios"])


def _intake(entry: dict, years: list[str]) -> float:
    return sum(entry["admitted"][y] for y in years) / len(years)


def build_cohorts(cfg: dict | None = None, scale: float = 1.0) -> pd.DataFrame:
    """One row per programme-year cohort that should get a makerspace course.

    Columns: cohort, programme, year, level, students, basis
    `basis` says how the number was obtained (sourced vs. which assumptions).
    """
    cfg = cfg or load_yaml("cohorts.yaml")
    a = cfg["assumptions"]
    yrs = a["intake_years"]
    rows = []

    copen_y1 = _intake(cfg["copen"]["COPEN"], yrs)
    copen_inflow = copen_y1 * a["copen_retention"] * a["copen_to_sci_share"]
    rows.append(("COPEN", 1, "bachelor", copen_y1, f"UHR mean {'/'.join(yrs)}"))

    y3 = {}
    for prog, e in cfg["civing"].items():
        n1 = _intake(e, yrs)
        n2 = n1 * a["retention_y1_y2"] + copen_inflow * a["copen_split"].get(prog, 0)
        n3 = n2 * a["retention_y2_y3"]
        y3[prog] = n3
        rows.append((prog, 1, "bachelor", n1, f"UHR mean {'/'.join(yrs)}"))
        rows.append((prog, 2, "bachelor", n2, "y1 x retention + COPEN inflow (assumed)"))
        rows.append((prog, 3, "bachelor", n3, "y2 x retention (assumed)"))

    for prog, e in cfg["masters"].items():
        ext = _intake(e, yrs) * a["master_showup"]
        internal = sum(y3[c] * a["civing_y3_to_sci_master"] * split.get(prog, 0)
                       for c, split in a["internal_flow"].items())
        m1 = ext + internal
        basis = "UHR x show-up + internal flow (assumed)"
        if e.get("placeholder"):
            basis = "PLACEHOLDER + internal flow (assumed)"
        rows.append((prog, 1, "master", m1, basis))
        rows.append((prog, 2, "master", m1 * a["master_retention_y1_y2"], "m1 x retention (assumed)"))

    df = pd.DataFrame(rows, columns=["programme", "year", "level", "students", "basis"])
    df["students"] = (df["students"] * scale).round().astype(int)
    df.insert(0, "cohort", df["programme"] + "-" + df["year"].astype(str))
    names = {**{k: v["name"] for k, v in cfg["civing"].items()},
             **{k: v["name"] for k, v in cfg["copen"].items()},
             **{k: v["name"] for k, v in cfg["masters"].items()}}
    df.insert(2, "name", df["programme"].map(names))
    return df.reset_index(drop=True)


def sections_for(students: int, section_size: int) -> int:
    return max(1, math.ceil(students / section_size))
