"""Convert per-device raw Parquet telemetry to derived CSVs.

This is the single central ingest step. Besides the format conversion it

  * keeps only rows in device state 0 (normal measuring), the only state in
    which the thermistor powers carry information (see
    ``data/metadata/states.csv``). Warm-up (1), sleep (5) and low-voltage (6)
    rows, and the null-state connectivity/battery heartbeats, are dropped;
  * applies the field correction for the deployed firmware's powerF/powerC
    swap (see ``sensor_correction`` and ``docs/field_correction.md``):
    ``powerC<->powerF`` are swapped so ``powerF`` is the FLOW thermistor and
    ``powerC`` the COMP thermistor, with a ``swap_corrected`` provenance flag;
  * adds ``comp_corrected`` (CH4 mole fraction) and ``flow_corrected`` (L/min,
    from the King's-law inverse).

The firmware's own ``flow`` and ``comp`` columns are corrupted by the swap and
are NOT carried into the output; analysis uses the ``*_corrected`` columns.
``battery_volt`` and ``rssi_dbm`` are carried but sparse: they arrive mostly on
the null-state heartbeat stream, which the state filter removes, and are non-null
on about 1.3% of state-0 rows.

Output schema is FIXED -- every emitted CSV has exactly ``OUTPUT_COLUMNS``, in
that order. The raw Parquet is not schema-stable (16 distinct column sets across
the fleet, some files carrying an extra ``msg`` column and some lacking
``temp``), so the output columns are explicitly reindexed rather than inherited.

Usage:  python3 src/derive.py [--raw DIR] [--out DIR] [--quiet]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sensor_correction as sc  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent

# The only device state in which flow and composition carry information.
KEEP_STATE = 0

# Fixed published schema. Order is meaningful: identity, environment, raw
# thermistor powers, then the two corrected measurements and the provenance flag.
OUTPUT_COLUMNS = [
    "timestamp",        # UTC sample time
    "state",            # device state code; always KEEP_STATE, kept as a record of the filter
    "temp",             # deg C
    "pressure",         # digester head pressure, mbar gauge
    "battery_volt",     # V; sparse, see module docstring
    "rssi_dbm",         # dBm; sparse, see module docstring
    "powerC",           # COMP-thermistor power, mW (swap-corrected)
    "powerF",           # FLOW-thermistor power, mW (swap-corrected)
    "flow_corrected",   # L/min, King's-law inverse on powerF
    "comp_corrected",   # CH4 mole fraction [0,1]; NaN during flow / outside 16-33 C
    "swap_corrected",   # provenance flag; guards against a second swap
]

# Columns that must be present in the raw file for the correction to be defined.
# `flow` is required because it is one of the two no-flow gates on
# comp_corrected (see sensor_correction.NO_FLOW_MAX).
REQUIRED_RAW = ("state", "powerC", "powerF", "temp", "flow")


def convert_file(parquet_file: Path, csv_file: Path) -> str:
    """Derive one day of one device. Returns a status string for the caller.

    Writes ``csv_file`` only on ``"ok"``; every other status leaves no file.
    """
    df = pd.read_parquet(parquet_file)
    if df.empty:
        return "empty"
    missing = [c for c in REQUIRED_RAW if c not in df.columns]
    if missing:
        # Two distinct cases, reported apart so a partial file is not filed as a
        # heartbeat one. Housekeeping-only files (connectivity/battery heartbeats
        # carrying no sensor payload) lack every sensor column and legitimately
        # derive nothing. A file missing only some of them carries real
        # measurements that cannot be corrected -- rarer, and worth naming.
        # Reported either way, never silently dropped.
        kind = "no-sensor-data" if len(missing) == len(REQUIRED_RAW) else "incomplete-schema"
        return f"{kind} (missing {','.join(missing)})"

    df = df[df["state"] == KEEP_STATE]
    if df.empty:
        # The device never reached normal measuring that day (e.g. slept or
        # warmed up only). Nothing here is usable, so no file is written.
        return "no-state-0-rows"

    df = sc.apply_swap_fix(df)
    df = sc.add_corrected_columns(df)

    out = df.reindex(columns=OUTPUT_COLUMNS)
    out.to_csv(csv_file, index=False)
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, default=BASE_DIR / "data" / "raw_data")
    ap.add_argument("--out", type=Path, default=BASE_DIR / "data" / "derived_data")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    ap.add_argument("--temp-window-scan", action="store_true",
                    help="print where the conc polynomial crosses zero (the source "
                         "of sensor_correction.TEMP_VALID) and exit")
    args = ap.parse_args()

    if args.temp_window_scan:
        lo, hi = sc.temp_window_crossings()
        pf, pc = sc._TEMP_WINDOW_REF_POWERS
        print(f"conc polynomial at the cohort median no-flow powers "
              f"(powerF={pf} mW, powerC={pc} mW):")
        print(f"  crosses zero at {lo:.2f} C and {hi:.2f} C")
        print(f"  TEMP_VALID = {sc.TEMP_VALID}  (rounded inward from those roots)")
        print(f"  lab_cal_envelope.T_C = (16.0, 33.0)  -- the FIT range, wider than the valid range")
        return 0

    written = 0
    skipped: list[tuple[Path, str]] = []
    for sn_dir in sorted(args.raw.glob("SN_*")):
        if not sn_dir.is_dir():
            continue
        out_dir = args.out / sn_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for parquet_file in sorted(sn_dir.glob("*.parquet")):
            csv_file = out_dir / parquet_file.with_suffix(".csv").name
            status = convert_file(parquet_file, csv_file)
            if status == "ok":
                written += 1
                if not args.quiet:
                    print(f"  {parquet_file.relative_to(args.raw)} -> {csv_file.name}")
            else:
                skipped.append((parquet_file.relative_to(args.raw), status))

    print(f"\nWrote {written} CSV files to {args.out}")
    if skipped:
        # Two distinct reasons, reported separately so a failed unit (no sensor
        # payload at all) is not confused with an idle day (never in state 0).
        by_reason: dict[str, dict[str, int]] = {}
        for path, status in skipped:
            if status.startswith("no-sensor"):
                reason = "no sensor payload"
            elif status.startswith("incomplete-schema"):
                reason = "incomplete sensor schema"
            else:
                reason = "no state-0 rows"
            dev = by_reason.setdefault(reason, {})
            dev[path.parent.name] = dev.get(path.parent.name, 0) + 1
        for reason, devs in sorted(by_reason.items()):
            print(f"Skipped {sum(devs.values())} file(s), {reason}:")
            for dev, n in sorted(devs.items()):
                print(f"  {dev}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
