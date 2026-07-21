"""Convert per-device raw Parquet telemetry to derived CSVs.

This is the single central ingest step. Besides the format conversion it applies
the field correction for the deployed firmware's powerF/powerC swap (see
``sensor_correction`` and ``docs/field_correction.md``):

  * ``powerC<->powerF`` are swapped so ``powerF`` is the FLOW thermistor and
    ``powerC`` the COMP thermistor, with a ``swap_corrected`` provenance flag;
  * ``comp_corrected`` (CH4 mole fraction) and ``flow_corrected`` (L/min, from
    the King's-law inverse) are added.

The firmware's own ``flow`` and ``comp`` columns are corrupted by the swap and
are NOT carried into the output; analysis uses the ``*_corrected`` columns.

Output schema is FIXED -- every emitted CSV has exactly ``OUTPUT_COLUMNS``, in
that order. The raw Parquet is not schema-stable (16 distinct column sets across
the fleet, some files carrying an extra ``msg`` column and some lacking
``temp``), so the output columns are explicitly reindexed rather than inherited.

Usage:  python3 src/derive.py [--raw DIR] [--out DIR]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sensor_correction as sc  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent

# Fixed published schema. Order is meaningful: identity, environment, device
# health, raw thermistor powers, then the two corrected measurements.
OUTPUT_COLUMNS = [
    "timestamp",        # UTC sample time
    "state",            # device state code, see data/metadata/states.csv
    "temp",             # deg C
    "pressure",         # digester head pressure
    "battery_volt",     # V
    "rssi_dbm",         # dBm
    "powerC",           # COMP-thermistor power, mW (swap-corrected)
    "powerF",           # FLOW-thermistor power, mW (swap-corrected)
    "flow_corrected",   # L/min, King's-law inverse on powerF
    "comp_corrected",   # CH4 mole fraction [0,1]; NaN during flow / outside 16-33 C
    "swap_corrected",   # provenance flag; guards against a second swap
]

# Columns that must be present in the raw file for the correction to be defined.
REQUIRED_RAW = ("powerC", "powerF", "temp")


def convert_file(parquet_file: Path, csv_file: Path) -> str:
    """Derive one day of one device. Returns a status string for the caller."""
    df = pd.read_parquet(parquet_file)
    if df.empty:
        return "empty"
    missing = [c for c in REQUIRED_RAW if c not in df.columns]
    if missing:
        # Housekeeping-only files (connectivity/battery heartbeats carrying no
        # sensor payload) legitimately lack these. Reported, never silently dropped.
        return f"no-sensor-data (missing {','.join(missing)})"

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
    args = ap.parse_args()

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
        by_device: dict[str, int] = {}
        for path, _ in skipped:
            by_device[path.parent.name] = by_device.get(path.parent.name, 0) + 1
        print(f"Skipped {len(skipped)} file(s) carrying no sensor payload:")
        for dev, n in sorted(by_device.items()):
            print(f"  {dev}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
