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
powers and carry their own temperature dependence, so the corrections take the
swap-fixed powers (`powerF`, `powerC`) and `temp` in °C directly; no temperature
detrending is applied first (it would double-correct for T).

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
  - `Q > 12` (past the lab cal max): the **pinned** extrapolation — hold the
    exponent at `n = 0.5733` (`calibration_card.json` ->
    `flow_calibration.cfd_kings_law_gas_scale.n`) and refit `(a, b)` to the lab
    anchors at Q = 4, 8, 12, giving `a = 24.62, b = 5.028`. This reproduces the
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
  King's law were fit on raw device-scale powers and carry their own T-terms;
  feeding temperature-detrended powers would double-correct. `comp_corrected`
  matching GT on raw powers confirms this.
- **Flow = King's-law hybrid** (not the deployed poly). It is physically
  grounded (CTA), monotonic, continuous, and extrapolates safely past Q=12 — the
  failure mode the correction targets. The deployed flow polynomial is not
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
  out (single calibration for the fleet).
- `temp` is the gas/sensor temperature in °C (confirmed: field range ~13–37 °C),
  the unit the deployed poly expects.

