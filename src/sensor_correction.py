"""Field-data correction for the deployed biogas sensor: undo the firmware
powerF/powerC swap and recompute flow + composition from raw thermistor powers.

Why this exists
---------------
The deployed firmware crossed the two thermistor ADC channels, so it fed the
``calc_flow`` / ``calc_ratio`` polynomials their power arguments SWAPPED. The
device therefore emits:

* ``powerC`` = the FLOW thermistor power (CTA, rises with flow), and
* ``powerF`` = the COMP thermistor power (cavity katharometer, flat/decreasing),

i.e. the OPPOSITE of what the column names imply, and its firmware ``flow``
column is noisy-wrong while its ``comp`` column is INVERTED. This cannot be
fixed in firmware (no OTA), so we correct in post-processing.

What we do
----------
1. :func:`apply_swap_fix` swaps ``powerC<->powerF`` so that after correction
   ``powerF`` holds the FLOW thermistor and ``powerC`` the COMP thermistor -- the
   convention the deployed polynomial actually expects. The device-native values
   are kept in the frame as ``powerC_raw`` / ``powerF_raw`` (``derive.py`` does
   not publish them: they are bit-identical to the swapped columns) and a
   ``swap_corrected`` provenance flag is set. The function REFUSES a frame that
   already carries the flag: applying the swap twice would silently restore the
   firmware's crossed assignment.
2. :func:`add_corrected_columns` adds:
   * ``comp_corrected`` -- CH4 mole fraction from the deployed conc polynomial
     applied in natural order on the swap-fixed RAW powers (this un-inverts it;
     lab RMSE 0.071). Composition is measured only when there is no flow, so it
     is populated only where BOTH flow indicators agree there is none -- the
     firmware's own flow state AND the recomputed ``flow_corrected`` -- and is
     NaN during flow; there it matches the reference analyser (see
     field_correction.md). The firmware gate alone is not sufficient: it forces
     flow to 0.0 for 60 s after every wake, regardless of the true value.
   * ``flow_corrected`` -- L/min from a King's-law inverse on the flow-thermistor
     RAW power: the lab CTA fit for Q<=12, the CFD-pinned extrapolation for Q>12.

The deployed 10-term FLOW polynomial is deliberately NOT implemented. It is
unphysical -- it has no zero, returning
~11 L/min at 25 C and ~22 L/min at 30 C with both thermistor powers at exactly
0 -- and it reproduced the firmware's own corrupted ``flow`` column at r ~ +0.97,
so it was a re-derivation of the corrupted signal rather than an independent
cross-check. Flow comes solely from the King's-law inverse, which has been
cross-validated at cohort level against an independent pressure-drawdown volume
estimate in the technical paper (per-plant median ratio ~1.00; see
``data/derived_data/README.md``). The CONC polynomial is the only path to
``comp_corrected``.

Neither function filters by device state. That is ``derive.py``'s job: it keeps
only state-0 rows before calling these, because outside state 0 the thermistor
powers reflect warm-up or sleep, not gas, and both corrected quantities come out
plausible-looking but meaningless.

All constants below are transcribed from the CFD calibration card, vendored into
this repository as ``data/metadata/calibration_card.json`` so the derivation is
reproducible from this repository alone. See ``docs/field_correction.md`` for the
full method, the limitations, and the device-vs-lab flow-power scale
reconciliation.

Units: powers in mW, temperature in deg C, flow in L/min, composition as CH4
mole fraction [0, 1].
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Deployed 10-term COMPOSITION polynomial
# (calibration_card.json -> deployed_polynomial.conc).
# form: v = C + PF*pf + PC*pc + T*t + PF2*pf^2 + PFPC*pf*pc + PFT*pf*t
#         + PC2*pc^2 + PCT*pc*t + T2*t^2
# with pf = FLOW-thermistor power, pc = COMP-thermistor power, t = T[deg C].
#
# The sibling `deployed_polynomial.flow` entry in the card is deliberately NOT
# transcribed here -- see the module docstring for why it was removed.
# ---------------------------------------------------------------------------
CONC_POLY = {
    "C": -0.45979628, "PF": 0.06357058, "PC": 0.04170046, "T": -0.05493335,
    "PF2": -0.00082725, "PFPC": 0.00621735, "PFT": -0.00652482,
    "PC2": -0.01915579, "PCT": 0.0355975, "T2": -0.01578462,
}
CONC_POLY_CLAMP = (0.0, 1.0)

# ---------------------------------------------------------------------------
# King's law flow calibration (calibration_card.json -> flow_calibration).
# Lab, device-scale CTA fit, valid Q <= 12 L/min: P_flow = a + b*Q^n.
# ---------------------------------------------------------------------------
LAB_KINGS = {"a": 22.39, "b": 6.665, "n": 0.5}

# CFD King's-law exponent (gas-scale fit, R^2 0.989). The pinned extrapolation
# keeps this exponent and refits (a, b) to the lab anchors at Q = 4, 8, 12 so it
# is anchored to device-scale data while carrying the CFD-validated shape.
CFD_KINGS_EXPONENT = 0.5732724102616522
_PINNED_ANCHORS_Q = (4.0, 8.0, 12.0)


def _fit_pinned() -> dict:
    """Refit (a, b) of P = a + b*Q^n to the lab King's-law anchors, holding the
    CFD exponent. Reproduces the card validation (P(Q=45) ~ 69.1 mW)."""
    q = np.array(_PINNED_ANCHORS_Q)
    p = LAB_KINGS["a"] + LAB_KINGS["b"] * q ** LAB_KINGS["n"]
    x = q ** CFD_KINGS_EXPONENT
    design = np.vstack([np.ones_like(x), x]).T
    (a, b), *_ = np.linalg.lstsq(design, p, rcond=None)
    return {"a": float(a), "b": float(b), "n": CFD_KINGS_EXPONENT}


PINNED_KINGS = _fit_pinned()

# Lab/pinned handover: the power at the lab cal max Q = 12 L/min.
_HANDOVER_POWER = LAB_KINGS["a"] + LAB_KINGS["b"] * 12.0 ** LAB_KINGS["n"]


# ---------------------------------------------------------------------------
# Pure functions (scalar- and array-friendly)
# ---------------------------------------------------------------------------
def _scalarize(value, sample):
    """Return a Python float when the original input was scalar."""
    return float(value) if np.isscalar(sample) else value


def _poly10(coef: dict, pf, pc, t):
    pf = np.asarray(pf, dtype=float)
    pc = np.asarray(pc, dtype=float)
    t = np.asarray(t, dtype=float)
    return (coef["C"] + coef["PF"] * pf + coef["PC"] * pc + coef["T"] * t
            + coef["PF2"] * pf ** 2 + coef["PFPC"] * pf * pc + coef["PFT"] * pf * t
            + coef["PC2"] * pc ** 2 + coef["PCT"] * pc * t + coef["T2"] * t ** 2)


def composition(power_flow_therm, power_comp_therm, temp_c):
    """CH4 mole fraction from the deployed conc polynomial (natural order).

    ``power_flow_therm`` is the FLOW thermistor power (PF arg), ``power_comp_therm``
    the COMP thermistor power (PC arg) -- on the swap-fixed columns that is
    ``powerF`` and ``powerC`` respectively. Clamped to [0, 1].
    """
    v = _poly10(CONC_POLY, power_flow_therm, power_comp_therm, temp_c)
    v = np.clip(v, *CONC_POLY_CLAMP)
    return _scalarize(v, power_flow_therm)


def kings_flow(power_flow_therm):
    """Flow [L/min] from the King's-law inverse on the FLOW-thermistor power.

    Q = ((P - a) / b)^(1/n), using the lab CTA fit for Q <= 12 (P <= 45.48 mW)
    and the CFD-pinned fit for Q > 12. Powers at/below the King's-law offset a
    map to zero flow. Result clamped to [0, 100].

    The lab and pinned branches meet within ~0.04 L/min at the handover (the
    pinned curve is an lstsq refit to the anchors); the micro-step is physically
    negligible.
    """
    p = np.asarray(power_flow_therm, dtype=float)

    base_lab = np.clip((p - LAB_KINGS["a"]) / LAB_KINGS["b"], 0.0, None)
    q_lab = base_lab ** (1.0 / LAB_KINGS["n"])

    base_pin = np.clip((p - PINNED_KINGS["a"]) / PINNED_KINGS["b"], 0.0, None)
    q_pin = base_pin ** (1.0 / PINNED_KINGS["n"])

    q = np.where(p <= _HANDOVER_POWER, q_lab, q_pin)
    q = np.clip(q, 0.0, 100.0)
    return _scalarize(q, power_flow_therm)


# ---------------------------------------------------------------------------
# DataFrame transforms (used by the ingest src/derive.py)
# ---------------------------------------------------------------------------
_SWAP_PAIRS = [("powerC", "powerF")]

# Composition is only valid in the device's no-flow window: the deployed conc
# poly is out of regime while gas moves past the katharometer cavity. TWO gates
# are applied, and a row must pass BOTH.
#
# 1. The firmware's own flow state (`flow` < NO_FLOW_MAX). Its magnitude is
#    corrupted by the swap, but its low/high distinction adapts per-device and it
#    correctly rejects a large population of low-power flowing rows that the
#    thermal gate alone keeps. Any threshold in (0, 2.0) selects the same rows
#    (the firmware reports flow below 2 L/min as exactly 0).
# 2. The recomputed flow (`flow_corrected` < NO_FLOW_MAX_CORRECTED, L/min).
#    Gate 1 CANNOT stand alone: for STABILIZATION_TIME_MS = 60 s after every
#    entry into state 0 the firmware forces `flow` to exactly 0.0 regardless of
#    the true value (main.cpp:979-985), and the device duty-cycles, so that
#    window follows every wake. Essentially every firmware-zero row in this
#    dataset sits inside it. On the ~9% of them where the user was already
#    cooking when the device woke, the flow thermistor is plainly cooled
#    (powerF ~40 mW against a ~23 mW no-flow baseline) and the conc poly is
#    extrapolated far out of regime, returning up to a clamped 1.0 CH4.
#    Gating on the thermal path as well removes those rows.
#
# See docs/field_correction.md and data/metadata/README.md.
NO_FLOW_MAX = 0.5
NO_FLOW_MAX_CORRECTED = 0.5

# The deployed conc polynomial was calibrated over the lab temperature envelope
# (calibration_card.json -> lab_cal_envelope.T_C). Its T^2 term (-0.0158) makes
# comp_corrected an inverse parabola in temperature (peak ~24 C, collapsing toward
# 0 below ~18 C / above ~30 C) -- a polynomial-extrapolation artefact, not real
# composition physics. Restrict comp_corrected to this window; it is NaN outside.
TEMP_VALID = (16.0, 33.0)


def apply_swap_fix(df: pd.DataFrame) -> pd.DataFrame:
    """Undo the firmware powerF/powerC swap. DEVICE-NATIVE INPUT ONLY.

    Preserves the device-native powers as ``powerC_raw`` / ``powerF_raw``, swaps
    ``powerC<->powerF`` so that afterwards ``powerF`` = FLOW thermistor and
    ``powerC`` = COMP thermistor, and sets the ``swap_corrected`` provenance flag.

    Raises ``ValueError`` on a frame that is already swap-corrected. This is a
    hard error rather than a silent no-op because applying the swap twice
    restores the firmware's crossed channel assignment -- flow and composition
    then come out physically wrong but entirely plausible-looking, with nothing
    to signal the error. Feed this function the raw Parquet, never a derived CSV.
    """
    if "swap_corrected" in df.columns and bool(df["swap_corrected"].any()):
        raise ValueError(
            "apply_swap_fix expects device-native data, but this frame is already "
            "swap-corrected (the swap_corrected flag is set). Re-applying the swap "
            "would silently restore the firmware's crossed channel assignment. "
            "Read the raw Parquet from data/raw_data/ instead of a derived CSV."
        )
    df = df.copy()

    # device-native provenance (powerC_raw = flow thermistor, powerF_raw = comp)
    if "powerC" in df.columns:
        df["powerC_raw"] = df["powerC"]
    if "powerF" in df.columns:
        df["powerF_raw"] = df["powerF"]

    for c, f in _SWAP_PAIRS:
        if c in df.columns and f in df.columns:
            df[c], df[f] = df[f].copy(), df[c].copy()

    df["swap_corrected"] = True
    return df


def add_corrected_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``comp_corrected`` and ``flow_corrected``.

    Expects a swap-fixed frame (``powerF`` = flow thermistor, ``powerC`` = comp
    thermistor) and ``temp`` in deg C. The deployed polynomial and the King's-law
    fit were calibrated on raw device-scale powers and carry their own
    temperature dependence, so no temperature detrending is applied first.
    """
    if not bool(df.get("swap_corrected", pd.Series([False])).all()):
        raise ValueError("add_corrected_columns requires apply_swap_fix first")
    if "flow" not in df.columns:
        raise ValueError(
            "add_corrected_columns requires the firmware 'flow' column: it is one "
            "of the two no-flow gates on comp_corrected (see NO_FLOW_MAX). Without "
            "it the frame would silently get composition on flowing rows."
        )
    df = df.copy()
    pf = df["powerF"]              # FLOW thermistor power (raw, swap-fixed)
    pc = df["powerC"]              # COMP thermistor power (raw, swap-fixed)
    t = df["temp"]
    flow_corrected = np.asarray(kings_flow(pf.to_numpy()), dtype=float)
    comp = np.asarray(composition(pf.to_numpy(), pc.to_numpy(), t.to_numpy()), dtype=float)

    # Composition only where BOTH flow indicators agree there is no flow. NaN in
    # either indicator counts as flowing, so an unknown regime never yields a
    # composition. See the NO_FLOW_MAX comment for why one gate is not enough.
    flowing = ~(df["flow"].to_numpy() < NO_FLOW_MAX)
    flowing |= ~(flow_corrected < NO_FLOW_MAX_CORRECTED)
    comp = np.where(flowing, np.nan, comp)

    # ...and only within the lab T-calibration window (see TEMP_VALID)
    tv = t.to_numpy()
    out_of_T = ~((tv >= TEMP_VALID[0]) & (tv <= TEMP_VALID[1]))  # NaN temp -> out
    comp = np.where(out_of_T, np.nan, comp)

    df["comp_corrected"] = comp
    df["flow_corrected"] = flow_corrected
    return df
