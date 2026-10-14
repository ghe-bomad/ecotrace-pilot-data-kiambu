"""Gap filling of sleeps during gas use.

Why this exists
---------------
The device duty-cycles: when its own on-board flow estimate stays at or below
2.5 L/min for 30 s it sleeps for 220 s and warms up for 50 s, so state-0 rows are
then 300 s apart. That on-board estimate uses the same crossed thermistor channels
as the firmware ``flow`` column (see ``docs/field_correction.md`` section 1), so
during cooking it often read low and the unit fell asleep while gas was still
flowing. In those stretches only one minute in five is measured. Across the
pilot's household units this left about 10 % of gas use unmeasured.

What we do
----------
A sleep is filled when it lies between two measured rows that are one sleep cycle
apart (``SLEEP_GAP_S``), both rows show gas in use (``flow_zero_cal`` at or above
``FLOWING_MIN``) and the flow after the sleep differs from the flow before it by
at most ``MAX_REL_CHANGE`` (relative to the flow before). The unmeasured time,
``gap - ROW_S`` seconds because each row already stands for its own minute, is
filled at the mean of the two flows. The litres are written to ``gap_fill_L`` on
the first row after the sleep; every other row gets 0.

Not filled: the start of use (the row before the sleep is quiet, so the start time
is unknown), sleeps whose flows differ by more than the tolerance, and gaps longer
than one sleep cycle (data gaps).

Daily volume with gap filling is therefore

    sum over rows with flow_zero_cal >= NO_FLOW_MAX of flow_zero_cal * 1 min
    + sum of gap_fill_L.

Do not add ``gap_fill_L`` to a time-weighted integral that already carries
readings across gaps: that counts the sleeps twice.

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
MAX_REL_CHANGE = 0.30        # largest |after - before| / before that is filled
SLEEP_GAP_S = (120, 330)     # a gap in (lo, hi] seconds is one sleep cycle (in practice 300 s)
ROW_S = 60                   # each state-0 row stands for one minute
NO_FLOW_MAX = 0.5            # L/min, rows below this are idle in the measured volume
HOLDOUT_MIN = 4              # minutes hidden per holdout window (as in a 300 s sleep)

SUMMARY_COLUMNS = ["sensor", "n_sleeps_during_use", "n_filled", "filled_L", "measured_L"]


def _neighbours(df: pd.DataFrame):
    """Sorted frame with the gap to, and the flow of, the previous row."""
    d = df.assign(_t=pd.to_datetime(df["timestamp"], utc=True)).sort_values("_t")
    gap = d["_t"].diff().dt.total_seconds()
    q = d["flow_zero_cal"].astype(float)
    return d, gap, q, q.shift()


def gap_fill_litres(df: pd.DataFrame) -> pd.Series:
    """Litres filled into the sleep before each row (0 where nothing is filled).

    ``df`` is one unit's state-0 rows with ``timestamp`` and ``flow_zero_cal``,
    in any order. The result is aligned to ``df.index``.
    """
    d, gap, q, prev = _neighbours(df)
    during = gap.gt(SLEEP_GAP_S[0]) & gap.le(SLEEP_GAP_S[1]) & (prev >= FLOWING_MIN) & (q >= FLOWING_MIN)
    steady = (q - prev).abs() <= MAX_REL_CHANGE * prev
    litres = ((prev + q) / 2 * (gap - ROW_S) / 60).where(during & steady, 0.0)
    return litres.reindex(df.index)


def holdout_check(df: pd.DataFrame, hide_min: int = HOLDOUT_MIN) -> dict:
    """Test the rule on continuous measuring runs of one or more units.

    Every window of ``hide_min`` consecutive measured minutes whose neighbours
    are both measured, one minute apart, and pass the rule is filled as if it had
    been a sleep, and the filled litres are compared with the measured ones.
    ``df`` may hold several units if it has a ``sensor`` column.
    """
    groups = df.groupby("sensor") if "sensor" in df.columns else [(None, df)]
    imputed, true = [], []
    for _, g in groups:
        d, gap, q, _ = _neighbours(g)
        qv = q.to_numpy()
        cont = (gap < 90).to_numpy()
        for i in range(len(qv) - hide_min - 1):
            j = i + hide_min + 1
            if not cont[i + 1:j + 1].all():
                continue
            a, b = qv[i], qv[j]
            if not (a >= FLOWING_MIN and b >= FLOWING_MIN and abs(b - a) <= MAX_REL_CHANGE * a):
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

    Runs over all of a unit's files at once, so sleeps that cross midnight are
    filled. Recomputed from ``timestamp`` and ``flow_zero_cal`` every time, and
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
        _, gap, q, prev = _neighbours(allrows)
        during = gap.gt(SLEEP_GAP_S[0]) & gap.le(SLEEP_GAP_S[1]) & (prev >= FLOWING_MIN) & (q >= FLOWING_MIN)
        start = 0
        for f, frame, n in zip(files, frames, lengths):
            frame["gap_fill_L"] = filled.iloc[start:start + n].to_numpy()
            frame.to_csv(f, index=False)
            start += n
        qa = allrows["flow_zero_cal"]
        rows.append({"sensor": sn, "n_sleeps_during_use": int(during.sum()),
                     "n_filled": int((filled > 0).sum()), "filled_L": float(filled.sum()),
                     "measured_L": float(qa[qa >= NO_FLOW_MAX].sum())})
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary.to_csv(derived_dir / "gap_fill_summary.csv", index=False)
    return summary
