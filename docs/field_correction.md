# Field-data correction: firmware powerF/powerC swap + recomputed flow & composition

*Method note, first written 2026-06-23 and revised for this release. Inputs
from the CFD calibration card (revision 2026-06-27), vendored in this repository
at [`data/metadata/calibration_card.json`](../data/metadata/calibration_card.json);
its companion notes `deployed_vs_kingslaw.md`, `cfd_field_gap_diagnosis.md` and
`cfd_calibration_plane.md` are part of the sensor-calibration analysis in the
technical paper's code — see the dataset README's
[Related work](../README.md#related-work).*

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
  gate is not enough. It is **also restricted to the lab temperature
  window 16–33 °C** (`TEMP_VALID`) and is `NaN` outside it: the conc poly's T²
  term makes `comp_corrected` an inverse parabola in temperature (peak ~24 °C,
  collapsing toward 0 below ~18 °C / above ~30 °C) — a polynomial-extrapolation
  artefact, not composition physics (§5).
- **`flow_corrected`** — L/min from the King's-law inverse on the flow-thermistor
  raw power `P=powerF`:
  - `Q ≤ 12` (P ≤ 45.48 mW): lab CTA fit `Q = ((P − 22.39) / 6.665)²`.
  - `Q > 12` (past the lab cal max): the **CFD-pinned** extrapolation —
    keep the CFD exponent `n = 0.5733` and refit `(a, b)` to the lab anchors
    (Q = 4, 8, 12), giving `a = 24.62, b = 5.028`. This reproduces the card's
    validation (P(Q=45) = 69.2 mW vs the card's 69.1). The two branches meet to
    within ~0.04 L/min at the handover (negligible).

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
  window** vs Dräger X-am 8000 reference CH₄ gives **RMSE 9.6 vol-% (≈0.096),
  bias −1.6 vol-%, r = 0.20** across the 14 sensors that have both a usable
  reference measurement and at least 50 composition rows (18 units survive the
  papers' exclusions; four of those have no reference reading taken before H₂S
  removal). The r is small because the GT spans only 50–59 vol-%. The firmware
  `comp` column was ~0.0–0.27 (inverted).

  *This supersedes an earlier RMSE of 7.3 vol-%, bias +0.1, quoted against a
  mis-stated sensor count of 18.* Under the identical pipeline the single-gate
  selector described below scored RMSE 8.0, bias −0.03, r 0.14; adding the
  second gate moves it to 9.6 / −1.6 / 0.20. The medians barely move (0.572 →
  0.560 pooled) — what changes is that a contaminated high-CH₄ tail which had
  been offsetting a small negative bias is gone. The corrected figure is the
  worse-looking one and the honest one.

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
- **Installation geometry biases the flow reading low, one-sided.** A worst-case
  CFD installation study (angled elbow pair, in-plane and out-of-plane routing)
  puts the shift at **−7 % in the cooking regime (Q14) to −12 % at Q45, always an
  under-read**, roughly independent of routing and **not** recovered by the flow
  conditioner. The mechanism is a velocity-deficient asymmetric profile at the
  CTA bead, not residual swirl. This is a real, one-sided, second-order
  contributor to per-plant scatter that stacks with the field-vs-lab power
  deficit below; it is *not* large enough to explain the extremes. Every field
  unit has some installation geometry, so treat `flow_corrected` as carrying an
  unremoved low bias of this order. The study is part of the technical paper's
  CFD analysis (`cfd/analysis/installation_study/`); it is quoted here because it
  bounds a limitation of this dataset's flow column.
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
  is therefore restricted to the lab T-calibration window (16–33 °C, `TEMP_VALID`)
  and is most reliable in its ~20–28 °C core. (Restricting the window leaves the
  validation essentially unchanged: RMSE 7.3 → 7.26 vol-%.)
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
- The CFD-pinned exponent (n=0.5733) is the gas-scale King's-law fit; it governs
  only the *shape* past Q=12, with `(a,b)` re-anchored to the lab device-scale
  data.

## 7. Not modelled

- The CFD↔device **absolute** power gap is **not** added back: we pin the
  absolute level and T-dependence to lab/device data rather than feeding raw CFD
  absolute powers into the deployed poly (which fails). The updated card models
  this gap as **one ~13 mW bead-hardware offset** (radiation + lead conduction at
  the pinned bead T = T_gas+20; ε=0.9 fixed, A=2.83e-5 m², G=0.393 mW/K,
  T_ref=293 K; reproduces the device to ±1.8 mW and is channel-independent), and
  offers a `device_matched_plane` (= CFD_gas + offset). The card states the
  device-matched and pin-to-lab routes are **equivalent**; this correction takes
  the pin-to-lab route (deployed poly + lab King's law on device powers), so the
  CFD absolute is used only for shape/extrapolation, never added in.
  Note this CFD↔device gap (~13 mW) is distinct from the **field-vs-lab**
  flow-thermistor deficit (~5.7 mW, §5), which the card does not address.
- The +7.9 % postflow buoyancy correction on the absolute composition power
  (`cfd_calibration_plane.md`) is not applied — `comp_corrected` is pinned to the
  device/lab katharometer via the deployed poly, and the GT validation already
  bounds the composition error (RMSE ~0.07).
- Humidity/condensation effects on either thermistor. The wet sub-study bounds
  rather than removes them: droplets >= 50 um gravity-settle to the pipe wall and
  never reach the beads, and at realistic airborne mist loading (LWC
  0.05-5 g/m3) the wet-bulb effect on `power_flow` is **+0.5 to +4 %,
  common-mode**, with composition essentially untouched (+0.15 %). Small, and
  not corrected for. (`cfd/analysis/wet_substudy/`.)
