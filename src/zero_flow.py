"""Per-unit zero-flow calibration of the flow channel.

Why this exists
---------------
``flow_corrected`` inverts King's law with one lab offset (a = 22.39 mW) for
every unit. In the field the no-flow power of the flow thermistor differs
between units by about 2 mW, rises with temperature by roughly 0.1 mW/K, and
on a few units drifts by more than 1 mW from week to week. Because flow goes as
the square of (P - a), a 1 mW offset error is a 14 % flow error at 5 L/min.

What we do
----------
Each unit's no-flow power is fitted from its own state-0 rows as

    P0(T) = p0 + slope * (T - T_REF),

with one slope per unit and the intercept p0 taken per week where the week has
enough no-flow rows (the unit's intercept otherwise). The power is then shifted
by the unit's departure from the FLEET zero before the same King's-law inverse:

    shift = P0_unit(T) - P0_fleet(T_REF)
    flow_zero_cal = kings_flow(powerF - shift)

Shifting relative to the fleet median, rather than replacing ``a`` with each
unit's measured zero, keeps the cohort flow scale of the lab calibration, which
is the scale the pressure-drawdown cross-check supports. Only the unit-to-unit,
week-to-week and temperature differences of the zero are removed. See
``docs/field_correction.md`` section 7.

Units: powers in mW, temperature in deg C, flow in L/min.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import sensor_correction as sc

# A row is no-flow when its power reads below the composition gate's 0.5 L/min
# (27.10 mW on the lab King's law).
P_NO_FLOW_MAX = (sc.LAB_KINGS["a"]
                 + sc.LAB_KINGS["b"] * sc.NO_FLOW_MAX_CORRECTED ** sc.LAB_KINGS["n"])
FLOWING_MIN = 2.0            # L/min, firmware deadband: at or above it gas is in use
# Minutes after the last flowing row during which the bead and the gas may still
# be cooling. The fitted zero moves by at most ~0.1 mW between 15 and 90 min, so
# the choice is not critical.
POST_FLOW_EXCLUDE_MIN = 45
T_REF = 25.0                 # deg C, temperature the fitted zero is quoted at
N_MIN = 500                  # no-flow rows a unit needs for its own zero
N_MIN_WEEK = 100             # no-flow rows a week needs for its own intercept

ZERO_TABLE_COLUMNS = ["sensor", "n_rows", "p0_ref", "slope", "t_lo", "t_hi", "resid_sd", "status"]


def select_no_flow(df: pd.DataFrame) -> pd.DataFrame:
    """No-flow rows of one unit: low power, known temperature, not just after cooking."""
    d = df.sort_values("timestamp")
    t = pd.to_datetime(d["timestamp"])
    last_flow = t.where(d["flow_corrected"] >= FLOWING_MIN).ffill()
    since = (t - last_flow).dt.total_seconds() / 60.0
    recent = since < POST_FLOW_EXCLUDE_MIN          # NaN (no earlier flow) is False
    keep = (d["powerF"] < P_NO_FLOW_MAX) & d["powerF"].notna() & d["temp"].notna() & ~recent
    return d[keep]


def fit_zero(noflow: pd.DataFrame, sensor: str, slope: float | None = None) -> dict:
    """Fit P0(T) = p0_ref + slope * (T - T_REF). A given ``slope`` is used as is."""
    n = len(noflow)
    if n < N_MIN:
        return dict(sensor=sensor, n_rows=n, p0_ref=np.nan, slope=np.nan,
                    t_lo=np.nan, t_hi=np.nan, resid_sd=np.nan, status="too-few-rows")
    t = noflow["temp"].to_numpy(float)
    p = noflow["powerF"].to_numpy(float)
    if slope is None:
        slope, p0 = np.polyfit(t - T_REF, p, 1)
    else:
        p0 = float(np.median(p - slope * (t - T_REF)))
    resid = p - (p0 + slope * (t - T_REF))
    return dict(sensor=sensor, n_rows=n, p0_ref=float(p0), slope=float(slope),
                t_lo=float(np.percentile(t, 5)), t_hi=float(np.percentile(t, 95)),
                resid_sd=float(resid.std()), status="ok")


def week_key(timestamps: pd.Series) -> pd.Series:
    """Monday (UTC) of each timestamp's week, as an ISO date string."""
    t = pd.to_datetime(timestamps, utc=True)
    day = t.dt.floor("D")
    return (day - pd.to_timedelta(day.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")


def fit_weekly_zero(noflow: pd.DataFrame, slope: float, n_min: int = N_MIN_WEEK) -> pd.Series:
    """Weekly intercept of the zero at T_REF, with the unit's slope held fixed.

    Weeks with fewer than ``n_min`` no-flow rows are left out; rows in those
    weeks use the unit's intercept.
    """
    r = noflow["powerF"].to_numpy(float) - slope * (noflow["temp"].to_numpy(float) - T_REF)
    g = pd.Series(r, index=week_key(noflow["timestamp"]).to_numpy())
    counts = g.groupby(level=0).size()
    return g.groupby(level=0).median()[counts >= n_min].rename("p0_ref")


def zero_shift(temp: np.ndarray, fit: dict, fleet_p0: float,
               p0_row: np.ndarray | None = None) -> np.ndarray:
    """Power shift [mW] of a unit's zero from the fleet zero at T_REF.

    ``p0_row`` gives a per-row intercept (the weekly zero) and falls back to the
    unit's intercept where it is NaN. Temperature is clipped to the unit's fitted
    range; a missing temperature uses the zero at T_REF. A unit without a fit
    gets no shift.
    """
    temp = np.asarray(temp, dtype=float)
    if fit["status"] != "ok":
        return np.zeros_like(temp)
    p0 = np.full_like(temp, fit["p0_ref"]) if p0_row is None else np.asarray(p0_row, float)
    p0 = np.where(np.isnan(p0), fit["p0_ref"], p0)
    tc = np.clip(temp, fit["t_lo"], fit["t_hi"])
    shift = p0 + fit["slope"] * (tc - T_REF) - fleet_p0
    return np.where(np.isnan(temp), p0 - fleet_p0, shift)


def flow_zero_cal(power: np.ndarray, shift: np.ndarray) -> np.ndarray:
    """Flow [L/min] from the King's-law inverse on the zero-shifted power."""
    return np.asarray(sc.kings_flow(np.asarray(power, float) - np.asarray(shift, float)), float)


def add_zero_cal(derived_dir: Path, slope: float | None = None) -> pd.DataFrame:
    """Fit every unit's zero, then fill ``flow_zero_cal`` in its derived CSVs.

    Writes ``zero_flow_table.csv`` (one row per unit, with the fleet zero) and
    ``zero_flow_weekly.csv`` (the weekly intercepts) into ``derived_dir``.
    Always recomputed from ``powerF`` and ``temp``, so running it twice gives
    the same files.
    """
    derived_dir = Path(derived_dir)
    units = {}
    for d in sorted(derived_dir.glob("SN_*")):
        files = sorted(d.glob("*.csv"))
        if files:
            units[d.name] = files

    fits, weekly = [], {}
    for sn, files in units.items():
        df = pd.concat((pd.read_csv(f, usecols=["timestamp", "temp", "powerF", "flow_corrected"])
                        for f in files), ignore_index=True)
        noflow = select_no_flow(df)
        fit = fit_zero(noflow, sn, slope=slope)
        fits.append(fit)
        if fit["status"] == "ok":
            weekly[sn] = fit_weekly_zero(noflow, fit["slope"])
    table = pd.DataFrame(fits, columns=ZERO_TABLE_COLUMNS)
    fleet_p0 = float(table.loc[table["status"] == "ok", "p0_ref"].median())
    table["fleet_p0_ref"] = fleet_p0

    for fit in table.to_dict("records"):
        wk = weekly.get(fit["sensor"], pd.Series(dtype=float))
        for f in units[fit["sensor"]]:
            # round_trip: the default parser can change the last digit of a float,
            # and every other column must come back exactly as derive wrote it
            df = pd.read_csv(f, float_precision="round_trip")
            p0_row = week_key(df["timestamp"]).map(wk).to_numpy(float)
            shift = zero_shift(df["temp"].to_numpy(float), fit, fleet_p0, p0_row=p0_row)
            df["flow_zero_cal"] = flow_zero_cal(df["powerF"].to_numpy(float), shift)
            df.to_csv(f, index=False)

    table.to_csv(derived_dir / "zero_flow_table.csv", index=False)
    rows = [(sn, week, p0) for sn, s in weekly.items() for week, p0 in s.items()]
    pd.DataFrame(rows, columns=["sensor", "week", "p0_ref"]).to_csv(
        derived_dir / "zero_flow_weekly.csv", index=False)
    return table
