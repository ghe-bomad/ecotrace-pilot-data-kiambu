# Field-data correction: firmware powerF/powerC swap + recomputed flow & composition

*Generated 2026-06-23 (WS-B). Inputs from the CFD calibration card, vendored in
this repository at [`data/metadata/calibration_card.json`](../data/metadata/calibration_card.json)
(+ its companion notes `deployed_vs_kingslaw.md`, `cfd_field_gap_diagnosis.md`
and `cfd_calibration_plane.md`, part of the sensor-calibration analysis in the
technical paper's code — see the dataset README's
[Related work](../README.md#related-work)).*

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
  matching the card's lab/field correlations (+0.565 / −0.636).
- Comp-thermistor mean power matches lab: field `powerF` ≈ **25.0 mW** vs lab
  comp 23.4 mW.
- Composition un-inversion: the firmware `comp` column averages ~0.05–0.20
  (inverted); the recomputed `comp_corrected` postflow median tracks the
  Dräger X-am 8000 reference (≈0.50 CH₄ mole fraction) across all 18
  non-excluded sensors.

## 2. The correction

### Stage A — undo the swap at ingest (`sensor_correction.apply_swap_fix`)

Device-native powers are preserved as `powerC_raw` / `powerF_raw`; then
`powerC↔powerF` and `powerC_adj↔powerF_adj` are swapped so that **after
correction `powerF` is the FLOW thermistor and `powerC` the COMP thermistor** —
the convention the deployed polynomial expects. A `swap_corrected=True` flag is
set; the operation is idempotent. The `*_adj` temperature de-trend (Elia's
curve) is computed on the device-native powers *before* the swap so each detrend
stays attached to its physical thermistor.

### Stage B — recompute flow & composition (`sensor_correction.add_corrected_columns`)

The deployed polynomials and King's law were calibrated on **raw, device-scale**
powers and carry their own temperature dependence, so the corrections take the
**raw** swap-fixed powers (`powerF`, `powerC`) and `temp` in °C — *not* the
`*_adj` columns (those would double-correct for T).

- **`comp_corrected`** — CH₄ mole fraction [0,1] from the deployed `conc`
  10-term polynomial in natural order (`PF=powerF`=flow thermistor,
  `PC=powerC`=comp thermistor). This alone un-inverts composition. vol-% =
  `comp_corrected × 100`. **Composition is measured only when there is no flow**:
  the conc polynomial is only valid in the device's no-flow regime (matching the
  Dräger X-am 8000 reference at RMSE 7.3 vol-% ≈ 0.073, bias ≈ 0), and is out of
  regime during cooking, so `comp_corrected` is **populated only where the device
  reports no flow (firmware `flow` < 0.5) and is `NaN` during flow** (`NO_FLOW_MAX`
  in `sensor_correction.py`). It is **also restricted to the lab temperature
  window 16–33 °C** (`TEMP_VALID`) and is `NaN` outside it: the conc poly's T²
  term makes `comp_corrected` an inverse parabola in temperature (peak ~24 °C,
  collapsing toward 0 below ~18 °C / above ~30 °C) — a polynomial-extrapolation
  artefact, not composition physics (§5). See §3 for the regime selector.
- **`flow_corrected`** — L/min from the King's-law inverse on the flow-thermistor
  raw power `P=powerF`:
  - `Q ≤ 12` (P ≤ 45.48 mW): lab CTA fit `Q = ((P − 22.39) / 6.665)²`.
  - `Q > 12` (past the lab cal max): the **CFD-pinned** extrapolation —
    keep the CFD exponent `n = 0.5733` and refit `(a, b)` to the lab anchors
    (Q = 4, 8, 12), giving `a = 24.62, b = 5.028`. This reproduces the card's
    validation (P(Q=45) = 69.2 mW vs the card's 69.1). The two branches meet to
    within ~0.04 L/min at the handover (negligible).
- **`flow_corrected_poly`** — L/min from the deployed `flow` polynomial, kept as
  an in-envelope cross-check (better in-envelope RMSE, but its high-flow
  extrapolation is an untrusted 10-term polynomial).

### Derived CSV schema (per row, `data/derived_data/SN_*/*.csv`)

```
timestamp, state, temp(°C), pressure, battery_volt, mem_free, rssi_dbm,  # passthrough
flow, comp,                          # FIRMWARE output — corrupted, reference only
powerC_raw, powerF_raw,              # device-native (flow therm / comp therm)
powerC, powerF, powerC_adj, powerF_adj,   # SWAP-CORRECTED (powerF=flow, powerC=comp)
swap_corrected,                      # provenance flag (True)
comp_corrected,                      # CH4 mole fraction  [PRIMARY composition]
flow_corrected,                      # L/min, King's-law  [PRIMARY flow]
flow_corrected_poly                  # L/min, deployed poly cross-check
```

### Stage C — repo-wide migration

Analysis code was migrated to the corrected columns: `flow → flow_corrected`,
`comp → comp_corrected`, and the in-house linear CH₄ calibration
(`common.powerC_to_ch4`, `CH4 = −0.41·powerC_adj + 63.7`) is **deprecated** —
it was fit on the old `powerC_adj`, which was the flow thermistor; post-swap
that column is the comp thermistor, so the fit no longer applies. CH₄ now comes
from `comp_corrected × 100`. Firmware `flow`/`comp` are kept only for
before/after illustration.

## 3. Validation & the composition window

- **Composition:** the per-sensor median `comp_corrected` in the **low-flow
  window** vs Dräger X-am 8000 reference CH₄ across the 18 non-excluded sensors gives
  **RMSE 7.3 vol-% (≈0.073), bias +0.1 vol-%, r small** (the GT spans only
  50–59 vol-%). This matches the card's stated conc RMSE 0.071. The firmware
  `comp` column was ~0.0–0.27 (inverted). The three worst residuals (SN_01005,
  01010, 01018) are exactly the already-excluded broken units.

- **The regime selector is the firmware `flow` state.** `comp_corrected` only
  recovers GT in the device's low-flow/composition regime, identified by
  `firmware flow < 0.5`. This uses the firmware `flow` purely as the device's
  own **mode indicator** (low vs high — its dominant term is the flow-thermistor
  power, so it tracks the regime even though its *magnitude* is corrupted), and
  it adapts per-device (a fixed flow-thermistor-power threshold would not, given
  the per-device offset in §5). Sub-windows checked: broad low-flow → RMSE
  7.3; long "resting" → 8.1; the narrow 5–45 min post-cooking transient → 17.0
  (the flow thermistor has not yet cooled, biasing the poly). So composition is
  read over the **broad low-flow window**, not the post-cooking transient.
  Flow *magnitudes* everywhere else use `flow_corrected`; only this regime
  *selection* uses firmware `flow`.
- **Flow shape:** `flow_corrected` rises monotonically with the flow-thermistor
  power, continuous through the lab→pinned handover, and clamps sensibly.
- **Unit tests:** `src/test_sensor_correction.py` (17 tests) pin the King's-law
  round-trip, the pinned Q=45→69.1 mW validation, swap idempotency / raw
  preservation, the no-flow-only composition masking, and the end-to-end SN_01001
  un-inversion.

## 4. Decisions made (flagged, not guessed)

- **Raw vs `_adj` powers:** corrections use **raw** powers. The deployed poly
  and King's law were fit on raw device-scale powers and carry their own
  T-terms; feeding the T-detrended `_adj` would double-correct. `comp_corrected`
  matching GT on raw powers confirms this.
- **Flow primary = King's-law hybrid** (not the deployed poly). It is physically
  grounded (CTA), monotonic, continuous, and extrapolates safely past Q=12 — the
  failure mode the correction targets. `flow_corrected_poly` is provided as a
  cross-check.
- **Handover at Q=12** (P=45.48 mW), continuous to ~0.04 L/min.

## 5. Limitations

- **No field flow ground truth.** There is no tipping-bucket / reference flow in
  the telemetry, so the **absolute** flow scale cannot be pinned. This is the
  single largest open item.
- **Flow-thermistor device-vs-lab power deficit (≈5.7 mW).** On cooking rows the
  field flow-thermistor power averages **34.9 mW** vs the lab CTA reference
  **40.6 mW** (Q 4–12). King's law, pinned to the lab absolute, therefore
  under-reads flow: cooking-window `flow_corrected` ≈ **5.4 L/min** vs
  `flow_corrected_poly` ≈ **12.0 L/min** (≈ the firmware magnitude). The truth
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
- **In-house single-thermistor calibration is weak.** The linear
  median-no-flow-`powerC_adj` → CH₄ cross-check, with the broken sensor 01018
  correctly excluded, has r ≈ 0 — its apparent correlation was driven by that
  one hardware-failed unit. This is why the production composition uses the
  two-thermistor deployed polynomial (`comp_corrected`), not a single thermistor.

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
- Humidity/condensation effects on either thermistor.
