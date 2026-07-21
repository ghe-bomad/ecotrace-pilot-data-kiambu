# Derived data

**This directory is intentionally empty except for this file.** The derived CSVs
are not committed; they are reproduced from `data/raw_data/` with:

```bash
python3 src/derive.py
```

That writes `data/derived_data/SN_XXXXX/YYYY-MM-DD.csv`: 2,828 files, about
367 MB, roughly 40 s on a laptop. Keeping them out of the repository avoids
shipping 367 MB of data that is a pure function of the 207 MB of Parquet already
tracked here.

## Schema

Every emitted CSV has exactly these 11 columns, in this order. The schema is
fixed by `OUTPUT_COLUMNS` in `src/derive.py` and reindexed explicitly, because
the raw Parquet is *not* schema-stable (16 distinct column sets across the fleet;
some files carry an extra `msg` column, some lack `temp`).

| column | unit | description |
|---|---|---|
| `timestamp` | UTC | sample time; nominal cadence 60 s |
| `state` | code | device state, see [`../metadata/states.csv`](../metadata/states.csv) |
| `temp` | °C | sensor-body temperature |
| `pressure` | mbar | digester head pressure, gauge |
| `battery_volt` | V | battery terminal voltage |
| `rssi_dbm` | dBm | cellular signal strength |
| `powerC` | mW | **COMP**-thermistor power, swap-corrected |
| `powerF` | mW | **FLOW**-thermistor power, swap-corrected |
| `flow_corrected` | L/min | flow, King's-law inverse on `powerF` |
| `comp_corrected` | mole fraction | CH₄ in [0, 1] |
| `swap_corrected` | bool | provenance flag, always `True` (see below) |

Small negative `pressure` values occur and are consistent with un-zeroed sensors
rather than real suction.

The firmware's own `flow` and `comp` columns are **not** carried through. Both are
corrupted by the channel swap described in [`../../docs/field_correction.md`](../../docs/field_correction.md):
`flow` is magnitude-wrong and `comp` is inverted. Use the `*_corrected` columns.

## `swap_corrected` is load-bearing

The deployed firmware crossed the two thermistor ADC channels, so the device's
`powerC` actually carries the FLOW thermistor and vice versa. `derive.py` undoes
this once, on read from Parquet, and sets `swap_corrected = True`.

The flag is what stops the correction being applied twice. `apply_swap_fix()`
raises `ValueError` on any frame carrying it. Re-applying the swap would restore
the firmware's crossed assignment, and the result would be physically wrong but
entirely plausible-looking, with nothing to signal the error. **Derive from the
raw Parquet; never feed a derived CSV back through the correction.**

## Why `comp_corrected` is NaN on about 39% of rows

Measured over 480,256 rows. Causes are exclusive, first one wins:

| cause | share of all rows |
|---|---|
| row carries no sensor payload at all | 28.97% |
| gas was flowing (`flow ≥ 0.5`) | 9.48% |
| temperature outside 16 to 33 °C | 0.96% |
| **populated** | **60.60%** |

Most of the gap is not a measurement failure. The device posts connectivity and
battery heartbeats on a slower cadence than the sensor stream, and those rows have
no sensor payload to compute anything from. The same 29% gap makes `pressure`,
`state`, `temp`, `powerC` and `powerF` null on exactly the same rows.

The flow gate is by design: the katharometer reads composition only in still gas,
so cooking windows are excluded. **On rows that are genuine sensor readings,
`comp_corrected` is populated 85% of the time.**

## Validity limits

Neither limit is enforced by the code. Both columns are emitted outside them.

- **Composition.** The conc polynomial was calibrated over `T ∈ [16, 33] °C` and
  `X_CH₄ ∈ [0.3, 0.7]`. Outside the temperature window `comp_corrected` is set to
  NaN; the composition range is *not* clipped. Validated against a Dräger X-am
  8000 reference analyser at RMSE ≈ 7.3 vol-%, in the no-flow window only.
- **Flow.** The King's-law fit covers `Q ∈ [4, 12] L/min`. Above 12 L/min a
  CFD-pinned extrapolation is used. On state-0 rows, 7.1% of samples and **31% of
  integrated volume** come from above that ceiling, and `powerF` reaches 2.36
  times the highest calibrated power.

## What `flow_corrected` can and cannot claim

No reference flow meter was installed in the field, so absolute accuracy is not
established *per household*. It has, however, been cross-validated at cohort
level against an independent pressure-drawdown volume estimate (`ΔV = C·Δp` from
dome compliance, which is composition-blind and shares no sensor or calibration
constant with the thermal path):

Based on 1,613 matched device-days from 15 plants:

- summarising **per-plant medians** across the 15 plants: median **1.00**, IQR
  0.71 to 1.15, range 0.42 to 1.83
- at the level of **individual device-days**: median 0.90, IQR 0.57 to 1.33

Quote the per-plant figure for cohort agreement and the device-day figure for
what a single household-day is worth; they are different populations and the
per-plant one is the tighter of the two. The spread is attributed mainly to
per-plant compliance `C` (silt accumulation, build quality, dome geometry),
which biases the *pressure* side.

This agreement holds *including* the 31% of volume above the calibration
ceiling, so the extrapolation is supported end to end rather than merely assumed.

**This comparison is not reproducible from this repository.** The pressure-based
volume derivation and the comparison against `flow_corrected` both live in the
companion analysis repository, `ecotrace-pilot-analysis-kiambu`. The numbers are
quoted here so that anyone using `flow_corrected` knows what evidence supports
it; verify them there. `flow_corrected` itself depends on none of it. It is a
function of the raw Parquet alone.

Two limits on the underlying fit are worth stating plainly. The King's-law
exponent `n = 0.5` was **assumed, not fitted**. And the advertised R² = 0.9992 is
two parameters against three group means; on the 540 underlying raw bench rows it
is R² = 0.783, RMSE 2.10 mW.

Treat cohort-level and temporal results as sound; treat single-household absolute
volumes as uncertain.

## Removed columns

Earlier revisions emitted 19 columns. Removed, with reasons:

- `powerC_raw`, `powerF_raw`: bit-identical to `powerF` and `powerC` respectively
- `powerC_adj`, `powerF_adj`: exactly recomputable (max error 7e-15), and their
  temperature-detrend coefficients had no traceable provenance
- `flow_corrected_poly`: the deployed 10-term flow polynomial is unphysical. It
  has no zero, returning about 11 L/min at 25 °C with both powers at exactly 0,
  and it reproduced the firmware's own corrupted `flow` column at r ≈ +0.97, so
  it was a re-derivation of the corrupted signal rather than an independent check
- `flow`, `comp`: firmware originals, corrupted by the channel swap
- `mem_free`: device heap telemetry, no analytical use
