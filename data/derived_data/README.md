# Derived data

**This directory is intentionally empty except for this file.** The derived CSVs
are not committed; they are reproduced from `data/raw_data/` with:

```bash
python3 src/derive.py
```

That writes `data/derived_data/SN_XXXXX/YYYY-MM-DD.csv`: 2,715 files, 841,742
rows, about 88 MB, roughly 15 s on a laptop. Keeping them out of the repository
avoids shipping a pure function of the 207 MB of Parquet already tracked here.

## Only state 0 is derived

`derive.py` keeps rows in device state 0 (normal measuring) and drops everything
else before the correction runs: warm-up (1), sleep (5), low voltage (6) and the
null-state connectivity/battery heartbeats. 841,742 of the 5,499,810 raw rows
(15.3%) survive. State 0 is the only state in which the thermistor powers
respond to gas; in the others the King's-law inverse and the composition
polynomial return plausible-looking but meaningless values (see
[`../metadata/README.md`](../metadata/README.md) for what each state does).

A day without a single state-0 row produces no file. Of the 2,828 raw files that
carry sensor payload, 113 are skipped for that reason, 31 of them on SN_01003 and
30 on SN_01009, both low-use plants. A further 147 files on SN_01018 derive
nothing for a different reason: 146 are heartbeat-only, carrying no sensor
payload at all, and one (`2026-02-04`) carries real measurements but lacks the
`temp` column that both corrections need. `derive.py` reports those two cases
separately and lists every skipped file with its reason.

The `state` column is kept, always 0, as a record that the filter was applied.
Downstream code that filters on it is a harmless no-op.

## Schema

Every emitted CSV has exactly these 11 columns, in this order. The schema is
fixed by `OUTPUT_COLUMNS` in `src/derive.py` and reindexed explicitly, because
the raw Parquet is *not* schema-stable (16 distinct column sets across the fleet;
some files carry an extra `msg` column, some lack `temp`).

| column | unit | description |
|---|---|---|
| `timestamp` | UTC | sample time; nominal cadence 60 s |
| `state` | code | device state, always 0 |
| `temp` | °C | sensor-body temperature |
| `pressure` | mbar | digester head pressure, gauge |
| `battery_volt` | V | battery terminal voltage; sparse, see below |
| `rssi_dbm` | dBm | cellular signal strength; sparse, see below |
| `powerC` | mW | **COMP**-thermistor power, swap-corrected |
| `powerF` | mW | **FLOW**-thermistor power, swap-corrected |
| `flow_corrected` | L/min | flow, King's-law inverse on `powerF` |
| `comp_corrected` | mole fraction | CH₄ in [0, 1]; NaN unless both flow gates agree there is no flow, and outside 16 to 33 °C |
| `swap_corrected` | bool | provenance flag, always `True` (see below) |

Small negative `pressure` values occur (1.5% of rows) and are consistent with
un-zeroed sensors rather than real suction.

The firmware's own `flow` and `comp` columns are **not** carried through. Both are
corrupted by the channel swap described in [`../../docs/field_correction.md`](../../docs/field_correction.md):
`flow` is magnitude-wrong and `comp` is inverted. Use the `*_corrected` columns.

`battery_volt` and `rssi_dbm` are carried but **sparse**: the device posts them
on a slower heartbeat that usually lands on a null-state row, which the state
filter removes, so they are non-null on about 1.3% of derived rows. For a
complete battery or signal history read the raw Parquet, where every heartbeat
is kept.

## `swap_corrected` is load-bearing

The deployed firmware crossed the two thermistor ADC channels, so the device's
`powerC` actually carries the FLOW thermistor and vice versa. `derive.py` undoes
this once, on read from Parquet, and sets `swap_corrected = True`.

The flag is what stops the correction being applied twice. `apply_swap_fix()`
raises `ValueError` on any frame carrying it. Re-applying the swap would restore
the firmware's crossed assignment, and the result would be physically wrong but
entirely plausible-looking, with nothing to signal the error. **Derive from the
raw Parquet; never feed a derived CSV back through the correction.**

## Why `comp_corrected` is NaN on 65% of rows

Measured over all 841,742 derived rows. Causes are exclusive, first one wins:

| cause | share of rows |
|---|---|
| firmware reports flow (`flow ≥ 0.5`) | 60.7% |
| `flow_corrected ≥ 0.5` L/min | 3.9% |
| temperature outside 16 to 33 °C | 0.3% |
| **populated** | **35.1%** |

The flow gates are by design: the katharometer reads composition only in still
gas, so cooking windows are excluded. The share is high because the device stays
in state 0 mainly while gas is moving; it sleeps after 30 s without flow, so
state-0 rows are dominated by cooking. Composition is read in the no-flow rows
that remain, which is the window the Dräger validation used.

**Two gates, not one.** The firmware forces `flow` to exactly 0.0 for 60 s after
every entry into state 0, regardless of the true value, so its zero means "we
are in the settling window", not "there is no flow". The second gate catches the
rows where the household was already cooking when the device woke: without it
29,370 rows (9.0% of all composition values) carried a composition while the
recomputed flow said gas was moving at a median 7.1 L/min, including 12,931
values pinned at exactly 1.000 CH₄. With both gates the published maximum is
0.792 and nothing reaches the clamp. See
[`../../docs/field_correction.md`](../../docs/field_correction.md) §3.

## Validity limits

The code enforces the state filter, the no-flow gate and the temperature window.
It does **not** enforce the flow or composition ranges of the calibration
envelope; both columns are emitted outside them.

- **Composition.** The conc polynomial was calibrated over `T ∈ [16, 33] °C` and
  `X_CH₄ ∈ [0.3, 0.7]`. Outside the temperature window `comp_corrected` is set to
  NaN; the composition range is *not* clipped. Validated against a Dräger X-am
  8000 reference analyser at RMSE ≈ 9.6 vol-% (bias −1.6, r 0.20, 14 sensors),
  in the no-flow window only.
- **Flow.** The King's-law fit covers `Q ∈ [4, 12] L/min`. Above 12 L/min a
  CFD-pinned extrapolation is used. 7.1% of derived samples and **31% of
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
volume derivation and the comparison against `flow_corrected` both live in
the technical paper's code (see the dataset README's [Related
work](../../README.md#related-work)). The numbers are quoted here so that
anyone using `flow_corrected` knows what evidence supports it; verify them
there. `flow_corrected` itself depends on none of it. It is a function of the
raw Parquet alone.

Two limits on the underlying fit are worth stating plainly. The King's-law
exponent `n = 0.5` was **assumed, not fitted**. And the advertised R² = 0.9992 is
two parameters against three group means; on the 540 underlying raw bench rows it
is R² = 0.783, RMSE 2.10 mW.

Treat cohort-level and temporal results as sound; treat single-household absolute
volumes as uncertain.

## What earlier internal revisions emitted, and why it is gone

Earlier revisions emitted every raw row with 19 columns. Removed, with reasons:

- rows outside state 0: see above
- `powerC_raw`, `powerF_raw`: bit-identical to `powerF` and `powerC` respectively
- `powerC_adj`, `powerF_adj`: temperature-detrended powers whose detrend
  coefficients had no traceable provenance; the deployed polynomial and the
  King's-law fit carry their own temperature terms
- `flow_corrected_poly`: the deployed 10-term flow polynomial is unphysical. It
  has no zero, returning about 11 L/min at 25 °C with both powers at exactly 0,
  and it reproduced the firmware's own corrupted `flow` column at r ≈ +0.97, so
  it was a re-derivation of the corrupted signal rather than an independent check
- `flow`, `comp`: firmware originals, corrupted by the channel swap
- `mem_free`: device heap telemetry, no analytical use
