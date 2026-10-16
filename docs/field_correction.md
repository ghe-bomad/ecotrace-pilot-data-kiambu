# Field-data correction: firmware powerF/powerC swap + recomputed flow & composition

*Method note, first written 2026-06-23 and revised for this release. The
calibration constants it applies are vendored at
[`data/metadata/calibration_card.json`](../data/metadata/calibration_card.json).
How those constants were established, and the simulation work behind them, is
the subject of the technical paper — see the dataset README's
[Related work](../README.md#related-work). This note documents what the
correction does to this dataset, not why the calibration takes the form it
does.*

## 1. The problem

The deployed firmware crossed the two thermistor ADC channels, so it fed the
`calc_flow` / `calc_ratio` polynomials their power arguments **swapped**. As a
result the device emits, contrary to the column names:

| device column | actually is | behaviour |
|---|---|---|
| `powerC` | the **FLOW** thermistor (CTA) | rises with flow |
| `powerF` | the **COMP** thermistor (cavity katharometer) | flat / decreasing |

and the firmware-derived `flow` column is **noisy-wrong** while `comp` is
**inverted**. This cannot be fixed in firmware (no OTA), so it is corrected in
post-processing at the single central ingest step (`src/derive.py`, which applies
`src/sensor_correction.py`).

### Evidence (empirical, this dataset)

- Field state=0, SN_01001: `corr(powerC, flow)= +0.60`, `corr(powerF, flow)= −0.56`
  — powerC tracks flow (flow thermistor), powerF anti-tracks (comp thermistor),
  matching the card's field correlations (+0.596 / −0.563) and its lab
  flow-thermistor correlation (+0.835).
- Comp-thermistor mean power matches lab: field `powerF` ≈ **25.0 mW** vs lab
  comp 23.4 mW.
- Composition un-inversion: the firmware `comp` column averages ~0.05–0.20
  (inverted); the recomputed `comp_corrected` postflow median tracks the
  Dräger X-am 8000 reference (≈0.50 CH₄ mole fraction) across the sensors with a
  reference measurement.

## 2. The correction

### Stage A — undo the swap at ingest (`sensor_correction.apply_swap_fix`)

Device-native powers are kept in the working frame as `powerC_raw` /
`powerF_raw`; then `powerC↔powerF` are swapped so that **after correction
`powerF` is the FLOW thermistor and `powerC` the COMP thermistor** — the
convention the deployed polynomial expects. A `swap_corrected=True` flag is set,
and `apply_swap_fix` refuses any frame that already carries it: a second swap
would restore the firmware's crossed assignment, and nothing in the numbers
would reveal it. The `*_raw` copies are not published; they are bit-identical to
the swapped columns.

### Stage B — recompute flow & composition (`sensor_correction.add_corrected_columns`)

The deployed polynomial and King's law were calibrated on **raw, device-scale**
powers, so the corrections take the swap-fixed powers (`powerF`, `powerC`) and
`temp` in °C directly; no temperature detrending is applied first. The
composition polynomial carries its own temperature terms, and detrending would
double-correct it. The King's-law fit has none: its offset `a` is a single lab
constant, which `flow_zero_cal` (§7) replaces with a per-unit,
temperature-dependent zero.

- **`comp_corrected`** — CH₄ mole fraction [0,1] from the deployed `conc`
  10-term polynomial in natural order (`PF=powerF`=flow thermistor,
  `PC=powerC`=comp thermistor). This alone un-inverts composition. vol-% =
  `comp_corrected × 100`. **Composition is measured only when there is no flow**:
  the conc polynomial is only valid in the device's no-flow regime and is out of
  regime during cooking, so `comp_corrected` is **populated only where *both*
  flow indicators agree there is no flow, and is `NaN` otherwise** —
  the firmware's own `flow` < 0.5 (`NO_FLOW_MAX`) **and** the recomputed
  `flow_corrected` < 0.5 L/min (`NO_FLOW_MAX_CORRECTED`). §3 explains why one
  gate is not enough. It is **also restricted to 17.5–30 °C** (`TEMP_VALID`) and
  is `NaN` outside it. That window is the conc polynomial's own positive range,
  not the lab fit envelope: its T² term makes `comp_corrected` an inverse
  parabola in temperature, and at representative no-flow powers the polynomial
  **crosses zero at 17.32 °C and 30.19 °C**, so outside those roots it can only
  return a clamped 0.0 (§5). Run `python3 src/derive.py --temp-window-scan` to
  reprint the roots from the coefficients.
- **`flow_corrected`** — L/min from the King's-law inverse on the flow-thermistor
  raw power `P=powerF`:
  - `Q ≤ 12` (P ≤ 45.48 mW): lab CTA fit `Q = ((P − 22.39) / 6.665)²`.
  - `Q > 12` (past the lab cal max): the **CFD-pinned** branch — hold the
    exponent at `n = 0.5733` (`calibration_card.json` ->
    `flow_calibration.cfd_kings_law_gas_scale.n`, from CFD simulations at
    Q = 6–45 L/min) and refit `(a, b)` to the lab anchors at Q = 4, 8, 12,
    giving `a = 24.62, b = 5.028`. Up to 45 L/min the shape is therefore set by
    the simulations, not extrapolated; only 2 derived samples lie above that. This reproduces the
    card's validation value P(Q=45) = 69.2 mW against its stated 69.1. The two
    branches meet to within ~0.04 L/min at the handover (negligible).

The deployed 10-term **flow** polynomial is not used. It has no zero (about
11 L/min at 25 °C with both thermistor powers at exactly 0) and it reproduces
the firmware's own corrupted `flow` column at r ≈ +0.97, so it would be a
re-derivation of the corrupted signal rather than an independent estimate.

### Stage C — keep state 0 only (`derive.py`)

The thermistor powers respond to gas only in device state 0 (normal measuring).
During warm-up (state 1) both heaters are driven hard to reach setpoint and the
King's-law inverse reads that as flow — a median of 0.04 L/min but 27 L/min at
the 90th percentile — on rows where the firmware itself reports zero; on an
unfiltered derivation 56% of the integrated volume sat in state 1. In sleep
(state 5) both powers are zero and the composition polynomial clamps to exactly
0, which made the unfiltered per-sensor median of `comp_corrected` 0.0 for every
sensor (RMSE 53.8 vol-% against the Dräger reference). `derive.py` therefore
drops every row not in state 0 before applying stages A and B: 841,742 of the
5,499,810 raw rows (15%) remain. See
[`data/metadata/states.csv`](../data/metadata/states.csv).

### Derived CSV schema (per row, `data/derived_data/SN_*/*.csv`)

```
timestamp, state, temp(°C), pressure,   # passthrough; state is always 0
battery_volt, rssi_dbm,                  # passthrough; sparse (heartbeat stream), ~1.3% non-null
powerC, powerF,                          # SWAP-CORRECTED (powerF=flow, powerC=comp), mW
flow_corrected,                          # L/min, King's-law inverse on powerF
flow_zero_cal,                           # L/min, flow_corrected with a per-unit zero (§7)
comp_corrected,                          # CH4 mole fraction; NaN unless BOTH flow gates
                                         #   agree no-flow, and outside 16-33 °C
swap_corrected                           # provenance flag (True)
```

The firmware `flow` and `comp` columns are not carried through (corrupted by the
swap); they remain in the raw Parquet.

## 3. Validation & the composition window

- **Composition:** the per-sensor median `comp_corrected` in the **no-flow
  window** vs Dräger X-am 8000 reference CH₄ gives **RMSE 8.5 vol-% (≈0.085),
  bias −0.5 vol-%, r = 0.17** across the 14 sensors that have both a usable
  reference measurement and at least 50 composition rows (18 units survive the
  papers' exclusions; four of those have no reference reading taken before H₂S
  removal). The r is small because the GT spans only 50–59 vol-%. The firmware
  `comp` column was ~0.0–0.27 (inverted).

  *This supersedes an earlier RMSE of 7.3 vol-%, bias +0.1, quoted against a
  mis-stated sensor count of 18.* Two changes account for the move. Adding the
  second flow gate took the single-gate 8.0 / −0.03 / 0.14 to 9.6 / −1.6 / 0.20,
  by removing a contaminated high-CH₄ tail that had been offsetting a negative
  bias in the medians. Narrowing the temperature window to the polynomial's
  positive range then took it to **8.5 / −0.5 / 0.17**, by removing rows that
  could only ever clamp to zero. Values pinned at exactly 0.0 fell from 5.0% of
  the published population to 2.2%, and none reach the ceiling.

- **The regime selector needs BOTH flow indicators.** `comp_corrected` only
  recovers GT in the device's no-flow/composition regime, and identifying that
  regime takes two independent gates:

  1. **The firmware `flow` state** (`firmware flow < 0.5`), used purely as the
     device's own **mode indicator**. Its magnitude is corrupted by the swap, but
     it adapts per-device (a fixed flow-thermistor-power threshold would not,
     given the per-device offset in §5) and it correctly rejects a large
     population of low-power flowing rows the thermal gate alone would keep.
     Dropping it and gating on `flow_corrected` alone is much worse: RMSE 15.9,
     bias −10.6.
  2. **The recomputed `flow_corrected`** (`< 0.5 L/min`). Gate 1 cannot stand
     alone. For `STABILIZATION_TIME_MS` = 60 s after **every** entry into state 0
     the firmware forces `flow` to exactly 0.0 regardless of the true value
     (`main.cpp:979-985`), and the device duty-cycles (sleep 220 s, warm-up 50 s,
     then normal), so that window follows every wake. Measured on this dataset:
     45.8% of state-0 rows carry `firmware flow == 0`, and **99.9% of them sit
     inside the settling window** — past 60 s the firmware never reports zero,
     because the device sleeps after 30 s without flow and so only stays awake
     while gas moves. "Firmware flow == 0" therefore means "we are in the
     settling window", not "there is no flow".

  On roughly 9% of those rows the household was already cooking when the device
  woke. The flow thermistor is plainly cooled there — `powerF` a median 40.1 mW
  against a 23.3 mW no-flow baseline, the two populations separating cleanly at
  27.1 mW — and the conc polynomial, extrapolated far out of regime, returned up
  to a clamped 1.0 CH₄. Before gate 2 was added, 29,370 rows (9.0% of all
  published composition values) were affected and carried 12,931 values pinned
  at exactly 1.000; with it, the published maximum is 0.792 and nothing reaches
  the clamp. A per-sensor **median** is insensitive to a 9% tail, which is why
  the single-gate version validated at a plausible-looking RMSE.

  Sub-windows checked under the single-gate selector: broad low-flow → RMSE
  7.3; long "resting" → 8.1; the narrow 5–45 min post-cooking transient → 17.0
  (the flow thermistor has not yet cooled, biasing the poly). So composition is
  read over the **broad no-flow window**, not the post-cooking transient.
  Flow *magnitudes* everywhere use `flow_corrected`.
- **Flow shape:** `flow_corrected` rises monotonically with the flow-thermistor
  power, continuous through the lab→pinned handover, and clamps sensibly.
- **Unit tests:** `src/test_sensor_correction.py` (25 tests) pin the King's-law
  round-trip, the pinned Q=45→69.1 mW validation, the swap guard and raw
  preservation, both no-flow gates and the temperature masking of
  `comp_corrected`, the state filter and fixed output schema of `derive.py`, the
  end-to-end SN_01001 un-inversion, and the published invariant that no
  composition survives on a row whose recomputed flow says gas is moving.

## 4. Decisions made (flagged, not guessed)

- **No temperature detrending before the correction.** The deployed poly and
  King's law were fit on raw device-scale powers. The poly carries its own
  T-terms, so feeding it temperature-detrended powers would double-correct;
  `comp_corrected` matching GT on raw powers confirms this. King's law has no
  T-term; its zero's temperature dependence is handled per unit by
  `flow_zero_cal` (§7), not by detrending.
- **Flow = King's-law hybrid** (not the deployed poly). It is physically
  grounded (CTA), monotonic and continuous, and above Q=12 it follows the CFD
  shape instead of running away, which is where the deployed polynomial fails. The deployed flow polynomial is not
  emitted at all (Stage B).
- **Handover at Q=12** (P=45.48 mW), continuous to ~0.04 L/min.

## 5. Limitations

- **No field flow ground truth.** There is no tipping-bucket / reference flow in
  the telemetry, so the **absolute** flow scale cannot be pinned. This is the
  single largest open item.
- **Below about 3 L/min, `flow_corrected` indicates flow but does not measure it.**
  At those rates the thermistor's heat loss is no longer dominated by the gas
  stream, and the lab calibration has no anchor there — its lowest point is
  4 L/min. Treat a reading below ~3 L/min as "gas is moving", not as a rate.
  Integrated daily volumes are not materially affected, because nearly all gas
  moves well above that boundary; this is a limit on instantaneous readings. The
  derivation of the boundary belongs to the technical paper.
- **Flow-thermistor device-vs-lab power deficit (≈5.7 mW).** On cooking rows the
  field flow-thermistor power averages **34.9 mW** vs the lab CTA reference
  **40.6 mW** (Q 4–12). King's law, pinned to the lab absolute, therefore
  under-reads flow: cooking-window `flow_corrected` ≈ **5.4 L/min** vs about
  **12.0 L/min** from the deployed flow polynomial (≈ the firmware magnitude,
  which is what that polynomial reproduces). The truth
  likely lies between; ~5 L/min is plausible for a single biogas burner, but
  this ~2× spread is unresolved without flow ground truth. The comp thermistor,
  by contrast, matches lab (25.0 vs 23.4 mW), so **composition is well-anchored
  while flow absolute is uncertain — the relative/temporal flow pattern is
  reliable, the absolute magnitude is not.** Downstream volumes and meter-sizing
  inherit this scale uncertainty.
- **Composition T-dependence is a polynomial artefact.** The deployed conc poly's
  T² term (−0.0158) makes `comp_corrected` an inverse parabola in temperature
  (peak ~24 °C), reproduced by evaluating the poly at fixed powers — i.e. it is
  the polynomial's T-extrapolation, not a real composition↔temperature effect.
  Below ~18 °C and above ~30 °C the estimate collapses toward 0. `comp_corrected`
  is therefore restricted to the polynomial's positive range (17.5–30 °C,
  `TEMP_VALID`) and is most reliable in its ~20–28 °C core. The lab fit envelope
  is wider (16–33 °C) and was used in earlier revisions; roughly three degrees at
  the top of it and one at the bottom lie past the polynomial's zero crossings and
  could only produce clamped zeros. Narrowing to the valid range improved the
  validation from RMSE 9.6 to 8.5 vol-%.
- **A single-thermistor composition calibration does not work.** A linear fit
  of median no-flow comp-thermistor power to reference CH₄, with the failed
  unit 01018 excluded, has r ≈ 0 — its apparent correlation was driven by that
  one unit. This is why composition uses the two-thermistor deployed polynomial
  (`comp_corrected`), not a single thermistor.

## 6. Assumptions

- The deployed polynomial and lab King's-law coefficients (one firmware build)
  apply to all field units; per-device manufacturing spread is not separated
  out (single calibration for the fleet). `flow_zero_cal` (§7) removes the
  per-device spread of the flow channel's **zero**, but not of its gain `b`.
- `temp` is the gas/sensor temperature in °C (confirmed: field range ~13–37 °C),
  the unit the deployed poly expects.

## 7. Per-unit zero-flow calibration (`flow_zero_cal`)

*Added in v1.1.0. Code: `src/zero_flow.py`, applied by `derive.py` after every
file has been derived.*

**Why.** `flow_corrected` inverts King's law with one lab offset,
`a = 22.39 mW`, for every unit. Measured in the field, the flow thermistor's
no-flow power at 25 °C ranges from 22.23 to 24.24 mW across the 22 units
(fleet median 23.35 mW), rises with temperature by 0.025 to 0.173 mW/K
depending on the unit, and on three household units (SN_01001, SN_01004,
SN_01017) drifts by more than 1.5 mW from week to week. Because flow goes as
the square of `P − a`, a 1 mW error in the zero is a 14 % flow error at
5 L/min and 18 % at 3 L/min.

**Which rows define the zero.** State-0 rows of the unit with
`powerF < 27.10 mW` (the power that reads 0.5 L/min, the same threshold as the
composition gate), a known temperature, and more than 45 min after the unit's
last row at or above 2 L/min, so that a bead or gas still cooling after
cooking does not bias the zero. The fitted zero changes by at most about
0.1 mW between 15 and 90 min of exclusion, so the choice is not critical.

**The model.** `P0(T) = p0 + slope·(T − 25 °C)`, with one slope per unit
(least squares on all its no-flow rows; standard errors ≤ 0.002 mW/K) and
the intercept `p0`, the median of the detrended no-flow power, taken per UTC
week where the week has at least 100 no-flow rows, or over all the unit's rows
otherwise. Temperature is clipped to the unit's
5th to 95th percentile of fitted temperatures, and a row without a
temperature uses the zero at 25 °C. Then

```
shift         = P0_unit(T) − P0_fleet(25 °C)
flow_zero_cal = kings_flow(powerF − shift)
```

where `P0_fleet` is the median of the units' intercepts. A unit with fewer
than 500 no-flow rows gets no shift (`flow_zero_cal = flow_corrected`); all 22
units in this release have enough.

**Why relative to the fleet.** Replacing `a` with each unit's measured zero
would lower every flow, because the fleet's field zero sits about 1 mW above
the lab offset, and there is no field flow reference to say which is right.
Shifting relative to the fleet keeps the cohort scale of the lab calibration,
the scale the pressure-drawdown cross-check supports, and removes only the
unit-to-unit, week-to-week and temperature differences of the zero. The fleet
scale moves little but is not fixed exactly, because the temperature term is
referenced to 25 °C and gas is usually warmer while it flows. Over the 17
household units (all days, Africa/Nairobi dates), total volume changes by
−3.4 % and the median of the household median daily volumes by +0.9 %; per
household the median daily volume changes by −11 % to +14 %.

**Research reactors.** SN_01005 and SN_01010, the two ILRI research digesters,
are fitted, but their no-flow power scatters three to five times more than on
the household units (residual SD 1.2 and 1.4 mW, against 0.2 to 0.8 mW), and
their total volume drops by about 27 %. Use their `flow_zero_cal` with
caution.

**Outputs.** Besides the column, `derive.py` writes
`data/derived_data/zero_flow_table.csv` (one row per unit: rows used,
`p0_ref`, `slope`, fitted temperature range, residual SD, status, and the
fleet zero) and `data/derived_data/zero_flow_weekly.csv` (the weekly
intercepts).

**Limits.**

- There is still no field flow reference. `flow_zero_cal` makes units more
  consistent with each other; it does not pin the absolute scale.
- The pressure-drawdown comparison cannot show the improvement: its scatter,
  set mainly by per-plant dome compliance, is larger than the change.
- The zero absorbs the CH₄-dependence of the bead's conduction offset only as
  a per-unit (or per-week) average, not row by row.
- The gain `b` is still the single lab value for every unit.
- The temperature slope is fitted at no flow and applied at all flows, which
  assumes the temperature effect on the bead is an additive offset.

## 8. Gap filling of unmeasured time next to gas use (`gap_fill_L`)

*Added in v1.1.0. Code: `src/gap_fill.py`, applied by `derive.py` after §7.*

**Why.** State-0 rows are one minute apart only while a unit stays awake. The
device duty-cycles: while measuring, it stays awake only if its own on-board flow
estimate exceeds 2.5 L/min at least once every 30 s; otherwise it sleeps for
220 s and warms up for 50 s, so a measured minute is followed by about four
unmeasured ones. Gas therefore goes unmeasured in three situations:

- **Sleeps during use.** The on-board estimate is `calc_flow` on the same
  crossed thermistor channels as the firmware `flow` column (§1), so during
  cooking it often read low and units fell asleep while gas was still flowing.
  On the 17 household units about 10,000 sleeps lie between two state-0 rows
  that both show gas in use; in 96 % of them the unit had been awake for a
  single logged minute, the flow before and after is nearly the same (median
  6.7 and 6.8 L/min), and dome pressure falls across these sleeps at a median
  0.19 mbar/min, against 0.22 mbar/min while measured and flowing and a rise of
  0.05 mbar/min across sleeps without use.
- **Starts and ends of use**, which fall at an unknown point inside a sleep.
- **Gaps longer than one cycle.** Most are not telemetry losses: the device kept
  logging sleep and warm-up rows, but no state-0 row was recorded, because the
  300 s cycle is a multiple of the 60 s logging interval and the awake window
  can be as short as 30 s, so for long stretches the logging tick can miss every
  awake window. The rest are telemetry losses (heartbeats only, or nothing).

**The rule.** Every row with gas in use (`flow_zero_cal` at or above 2 L/min)
stands in for half of the unmeasured time on each side of it, at its own flow,
up to one sleep cycle per side:

```
unmeasured time on one side = min(gap to the neighbouring state-0 row, 300 s) - 60 s   (at least 0)
gap_fill_L = flow_zero_cal x (unmeasured before + unmeasured after) / 2
```

Each row already stands for its own minute, hence the 60 s. The litres are
written to `gap_fill_L` on the row itself; rows below 2 L/min get 0. This gives:

- a sleep between two flowing rows filled at the mean of their two flows;
- the start or the end of use filled with half the sleep in which it fell, the
  expected value when the time of use is independent of where the device is in
  its cycle;
- for gaps longer than one cycle, only the cycle next to each measured row; the
  interior is left empty.

The interior of longer gaps is left empty on evidence: for gaps with gas in use
on both sides, the pressure fall across the gap, compared with each unit's
measured relation between flow and pressure fall, implies gas was drawn for a
median 68 % of 6 to 10 min gaps, 26 % of 15 to 20 min gaps and about 10 % of
gaps longer than 30 min. Use across such gaps is mostly separate sessions, not
continuous, and filling them at the neighbouring flow would overcount. Pressure
is used only for this check, not to fill any gap, so the pressure-drawdown
cross-check stays independent of the thermal volumes.

**Validation.** In continuous measuring runs every minute is logged, so the rule
for sleeps between flowing rows can be tested by hiding four logged minutes
between two kept ones (the length of a 300 s sleep) and filling them. Over all
22 units, 241,017 such windows: filled/logged median 1.000 (IQR 0.989 to 1.011),
total bias +0.06 %. `python3 src/derive.py --gap-fill-check` reruns the test.
The start/end half is an expected value and cannot be tested this way; the
pressure signal around starts and ends is dominated by line transients (a fast
drop when a burner opens, a rebound when it closes) and cannot calibrate it
either.

**Result.** On the 17 household units the fill adds 720 m³ to 2,569 m³ of
measured gas, 21.9 % of the total: 9.0 % from sleeps between flowing rows, 5.3 %
from starts and ends of use, and 7.5 % from the cycle next to longer gaps. The
share varies by unit, from 9 % (SN_01016) to 60 % (SN_01003, a low-use unit that
cycled through most of its use). On the ILRI research reactor SN_01005 it is 76 %.

**How to use it.** Daily gas volume is

```
sum of flow_zero_cal over rows with flow_zero_cal >= 0.5 L/min   (x 1 min)
+ sum of gap_fill_L
```

grouped by the day of each row. Do not add `gap_fill_L` to a time-weighted
integral that already carries readings across gaps (flow multiplied by the time
since the previous row): that counts the unmeasured time twice.

**Outputs.** The `gap_fill_L` column, and `data/derived_data/gap_fill_summary.csv`
with one row per unit: rows extended, litres filled, measured litres (rows at or
above 0.5 L/min), and hours in gaps longer than one cycle.

**Limits.**

- The interiors of gaps longer than one cycle are not filled, so volumes remain
  a lower bound wherever gas was used inside such a gap. The summary table gives
  the hours affected; analyses that need complete days should check telemetry
  completeness.
- The start/end fill is an expected value: right on average over many events,
  not for any single one.
- A stop and restart hidden entirely inside a single sleep between two flowing
  rows cannot be seen.
- Filled values inherit the uncertainty of `flow_zero_cal`.
