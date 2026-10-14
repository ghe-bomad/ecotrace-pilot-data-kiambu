"""Gap filling of unmeasured time next to gas use.

Why this exists
---------------
State-0 rows are one minute apart only while a unit stays awake. The device
duty-cycles: when its own on-board flow estimate stays at or below 2.5 L/min for
30 s it sleeps for 220 s and warms up for 50 s, so a measured minute is then
followed by about four unmeasured ones. That on-board estimate uses the same
crossed thermistor channels as the firmware ``flow`` column (see
``docs/field_correction.md`` section 1), so during cooking it often read low and
the unit fell asleep while gas was still flowing. Gas is also missed at the start
and end of use, which fall inside a sleep, and around telemetry gaps.

What we do
----------
Every row with gas in use (``flow_zero_cal`` at or above ``FLOWING_MIN``) stands
in for half of the unmeasured time on each side of it, at its own flow, up to one
sleep cycle per side:

    unmeasured time on one side = min(gap, CYCLE_S) - ROW_S   (at least 0)
    gap_fill_L = flow_zero_cal * (unmeasured before + unmeasured after) / 2

where ``gap`` is the time to the neighbouring state-0 row and each row already
stands for its own minute (``ROW_S``). This gives:

* a sleep between two flowing rows filled at the mean of their flows;
* the start and the end of use, which fall at an unknown point in a sleep,
  filled with half the sleep, the expected value when the time of use is
  independent of where the device is in its cycle;
* gaps longer than one cycle (unlogged wake-ups, telemetry losses) filled only
  for the cycle next to each measured row; their interior is left empty, because
  gas use across such gaps is mostly not continuous.

The litres are written to ``gap_fill_L`` on the row they belong to, so a day's
volume includes the gas next to its own rows. Daily volume with gap filling is

    sum over rows with flow_zero_cal >= NO_FLOW_MAX of flow_zero_cal * 1 min
    + sum of gap_fill_L.

Do not add ``gap_fill_L`` to a time-weighted integral that already carries
readings across gaps: that counts the unmeasured time twice.

``holdout_check`` tests the rule on continuous measuring runs by hiding four
measured minutes between two kept ones and comparing the filled volume with the
measured one. ``python3 src/derive.py --gap-fill-check`` prints it for the
dataset. See ``docs/field_correction.md`` section 8.

Units: flow in L/min, volume in L, time in s unless named otherwise.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FLOWING_MIN = 2.0            # L/min, firmware deadband: at or above it gas is in use
CYCLE_S = 300                # one sleep cycle: 30 s awake, 220 s asleep, 50 s warm-up
ROW_S = 60                   # each state-0 row stands for one minute
LONG_GAP_S = 330             # a gap longer than this spans more than one cycle
NO_FLOW_MAX = 0.5            # L/min, rows below this are idle in the measured volume
HOLDOUT_MIN = 4              # minutes hidden per holdout window (as in a 300 s sleep)

SUMMARY_COLUMNS = ["sensor", "n_rows_extended", "filled_L", "measured_L", "long_gap_h"]


def _sorted(df: pd.DataFrame):
    d = df.assign(_t=pd.to_datetime(df["timestamp"], utc=True)).sort_values("_t")
    gap_before = d["_t"].diff().dt.total_seconds()
    return d, gap_before, gap_before.shift(-1)


def _unmeasured_s(gap: pd.Series) -> pd.Series:
    """Unmeasured seconds next to a row on one side, capped at one sleep cycle."""
    return (gap.clip(upper=CYCLE_S) - ROW_S).clip(lower=0).fillna(0.0)


def gap_fill_litres(df: pd.DataFrame) -> pd.Series:
    """Litres of unmeasured gas each row stands in for (0 where the row is not in use).

    ``df`` is one unit's state-0 rows with ``timestamp`` and ``flow_zero_cal``,
    in any order. The result is aligned to ``df.index``.
    """
    d, gap_before, gap_after = _sorted(df)
    q = d["flow_zero_cal"].astype(float)
    half_s = (_unmeasured_s(gap_before) + _unmeasured_s(gap_after)) / 2
    litres = (q * half_s / 60).where(q >= FLOWING_MIN, 0.0)
    return litres.reindex(df.index)


def holdout_check(df: pd.DataFrame, hide_min: int = HOLDOUT_MIN) -> dict:
    """Test the rule on continuous measuring runs of one or more units.

    Every window of ``hide_min`` consecutive measured minutes whose neighbours
    are both measured, one minute apart, and in use is filled as if it had been
    a sleep (half at each neighbour's flow), and the filled litres are compared
    with the measured ones. ``df`` may hold several units if it has a ``sensor``
    column.
    """
    groups = df.groupby("sensor") if "sensor" in df.columns else [(None, df)]
    imputed, true = [], []
    for _, g in groups:
        d, gap_before, _ = _sorted(g)
        qv = d["flow_zero_cal"].astype(float).to_numpy()
        cont = (gap_before < 90).to_numpy()
        for i in range(len(qv) - hide_min - 1):
            j = i + hide_min + 1
            if not cont[i + 1:j + 1].all():
                continue
            a, b = qv[i], qv[j]
            if not (a >= FLOWING_MIN and b >= FLOWING_MIN):
                continue
            imputed.append((a + b) / 2 * hide_min)
            true.append(qv[i + 1:j].sum())
    imputed, true = np.asarray(imputed), np.asarray(true)
    if len(true) == 0:
        return {"n_windows": 0, "bias": np.nan, "ratio_median": np.nan, "ratio_iqr": (np.nan, np.nan)}
    ratio = imputed / true
    return {"n_windows": int(len(true)), "bias": float(imputed.sum() / true.sum() - 1),
            "ratio_median": float(np.median(ratio)),
            "ratio_iqr": (float(np.percentile(ratio, 25)), float(np.percentile(ratio, 75)))}


def _unit_files(derived_dir: Path) -> dict:
    units = {}
    for d in sorted(Path(derived_dir).glob("SN_*")):
        files = sorted(d.glob("*.csv"))
        if files:
            units[d.name] = files
    return units


def add_gap_fill(derived_dir: Path) -> pd.DataFrame:
    """Fill ``gap_fill_L`` in every unit's derived CSVs and write the unit summary.

    Runs over all of a unit's files at once, so gaps that cross midnight are
    handled. Recomputed from ``timestamp`` and ``flow_zero_cal`` every time, and
    re-reads each CSV with ``float_precision="round_trip"`` so the other columns
    come back exactly as written. Writes ``gap_fill_summary.csv`` into
    ``derived_dir`` and returns it.
    """
    derived_dir = Path(derived_dir)
    rows = []
    for sn, files in _unit_files(derived_dir).items():
        frames = [pd.read_csv(f, float_precision="round_trip") for f in files]
        lengths = [len(f) for f in frames]
        allrows = pd.concat(frames, ignore_index=True)
        filled = gap_fill_litres(allrows[["timestamp", "flow_zero_cal"]])
        start = 0
        for f, frame, n in zip(files, frames, lengths):
            frame["gap_fill_L"] = filled.iloc[start:start + n].to_numpy()
            frame.to_csv(f, index=False)
            start += n
        _, gap_before, _ = _sorted(allrows)
        qa = allrows["flow_zero_cal"]
        rows.append({"sensor": sn, "n_rows_extended": int((filled > 0).sum()),
                     "filled_L": float(filled.sum()),
                     "measured_L": float(qa[qa >= NO_FLOW_MAX].sum()),
                     "long_gap_h": float(gap_before[gap_before > LONG_GAP_S].sum() / 3600)})
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary.to_csv(derived_dir / "gap_fill_summary.csv", index=False)
    return summary
