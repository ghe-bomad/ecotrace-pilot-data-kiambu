"""Tests for the field-data swap correction + recomputed flow/composition.

Validation anchors come from the CFD calibration card, vendored in this
repository at data/metadata/calibration_card.json, and from the empirical field
check documented in docs/field_correction.md:

  - lab King's law  P = 22.39 + 6.665*Q^0.5   (Q <= 12)
  - CFD-pinned       keep n=0.5733, refit (a,b) to lab anchors Q=4,8,12; use for Q>12
  - pinned validation: at Q=45, P = 69.1 mW (card)
  - deployed conc poly on the swap-fixed columns recovers CH4 ~ 0.50 mole-fraction
    (Drager X-am 8000 reference for SN_01001 is ~0.497); the firmware-corrupted
    `comp` column reads ~0.20 (inverted).
  - composition is only valid in the no-flow window; comp_corrected is NaN
    unless BOTH flow indicators agree there is no flow -- the firmware's own
    `flow` state AND the recomputed `flow_corrected`.
"""
from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import sensor_correction as sc

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw_data"


# --------------------------------------------------------------------------
# King's law inversion (flow channel)
# --------------------------------------------------------------------------
def test_pinned_coefficients_refit_to_lab_anchors():
    """Pinned curve keeps the CFD exponent and is refit to the lab anchors."""
    assert sc.PINNED_KINGS["n"] == pytest.approx(0.5732724102616522)
    assert sc.PINNED_KINGS["a"] == pytest.approx(24.616, abs=0.05)
    assert sc.PINNED_KINGS["b"] == pytest.approx(5.028, abs=0.05)


def test_pinned_forward_matches_card_validation_at_Q45():
    """Card: CFD-pinned forward law gives 69.1 mW at Q=45."""
    p = sc.PINNED_KINGS["a"] + sc.PINNED_KINGS["b"] * 45.0 ** sc.PINNED_KINGS["n"]
    assert p == pytest.approx(69.1, abs=0.5)


def test_kings_flow_lab_branch_round_trip():
    """Within the lab envelope, P->Q inverts the lab King's law exactly."""
    P_q8 = 22.39 + 6.665 * 8.0 ** 0.5
    assert sc.kings_flow(P_q8) == pytest.approx(8.0, abs=1e-6)


def test_kings_flow_pinned_branch_round_trip():
    """Above Q=12 the pinned inverse recovers the flow used to make the power."""
    k = sc.PINNED_KINGS
    P_q20 = k["a"] + k["b"] * 20.0 ** k["n"]
    assert sc.kings_flow(P_q20) == pytest.approx(20.0, abs=1e-3)


def test_kings_flow_continuous_at_handover():
    """No meaningful jump at the Q=12 (P=45.478 mW) lab<->pinned handover.

    The pinned curve is an lstsq refit to the lab anchors, so it crosses Q=12 at
    ~11.965 while the lab branch gives exactly 12 -> an inherent ~0.04 L/min
    micro-step. Physically negligible; we only require there is no real jump.
    """
    P12 = 22.39 + 6.665 * 12.0 ** 0.5
    lo = sc.kings_flow(P12 - 1e-4)
    hi = sc.kings_flow(P12 + 1e-4)
    assert abs(hi - lo) < 0.1
    assert lo == pytest.approx(12.0, abs=1e-2)


def test_kings_flow_below_floor_is_zero():
    """Power at/below the King's law offset 'a' means no flow."""
    assert sc.kings_flow(22.39) == pytest.approx(0.0, abs=1e-9)
    assert sc.kings_flow(10.0) == 0.0


def test_kings_flow_vectorised():
    P12 = 22.39 + 6.665 * 12.0 ** 0.5
    out = sc.kings_flow(np.array([10.0, P12, 56.0]))
    assert out.shape == (3,)
    assert out[0] == 0.0
    assert out[1] == pytest.approx(12.0, abs=1e-2)
    assert out[2] > 12.0  # above handover uses pinned, still finite


# --------------------------------------------------------------------------
# Deployed polynomials (composition + flow cross-check)
# --------------------------------------------------------------------------
def test_composition_matches_documented_formula():
    """conc poly wiring matches the card's explicit 10-term form."""
    pf, pc, t = 30.0, 24.0, 25.0
    c = sc.CONC_POLY
    expected = (c["C"] + c["PF"] * pf + c["PC"] * pc + c["T"] * t
                + c["PF2"] * pf ** 2 + c["PFPC"] * pf * pc + c["PFT"] * pf * t
                + c["PC2"] * pc ** 2 + c["PCT"] * pc * t + c["T2"] * t ** 2)
    expected = min(max(expected, 0.0), 1.0)
    assert sc.composition(pf, pc, t) == pytest.approx(expected, abs=1e-9)


def test_composition_clamped_to_unit_interval():
    assert 0.0 <= sc.composition(0.0, 0.0, 0.0) <= 1.0
    assert 0.0 <= sc.composition(100.0, 100.0, 40.0) <= 1.0


def test_deployed_flow_polynomial_is_gone():
    """The deployed flow polynomial was removed: it has no zero (it returns
    ~11 L/min at 25 C with both thermistor powers at 0) and it reproduced the
    firmware's own corrupted `flow` column at r ~ +0.97 rather than providing an
    independent estimate. Flow now comes solely from the King's-law inverse."""
    assert not hasattr(sc, "FLOW_POLY")
    assert not hasattr(sc, "flow_poly")


# --------------------------------------------------------------------------
# Swap fix on a DataFrame
# --------------------------------------------------------------------------
def _toy_df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-02-01T00:00Z", "2026-02-01T00:01Z"]),
        "state": [0.0, 0.0],
        "temp": [25.0, 26.0],
        "flow": [11.0, 0.0],          # firmware (corrupted) flow
        "comp": [0.2, 0.2],           # firmware (inverted) comp
        "powerC": [38.0, 25.0],       # device: FLOW thermistor
        "powerF": [24.0, 26.0],       # device: COMP thermistor
    })


def test_swap_fix_swaps_power_columns():
    df = sc.apply_swap_fix(_toy_df())
    src = _toy_df()
    # after swap: powerF carries the FLOW thermistor (old powerC), powerC the COMP thermistor
    assert list(df["powerF"]) == list(src["powerC"])
    assert list(df["powerC"]) == list(src["powerF"])


def test_swap_fix_preserves_device_native_raw():
    df = sc.apply_swap_fix(_toy_df())
    src = _toy_df()
    assert list(df["powerC_raw"]) == list(src["powerC"])
    assert list(df["powerF_raw"]) == list(src["powerF"])
    assert bool(df["swap_corrected"].all())


def test_swap_fix_refuses_already_corrected_frame():
    """Re-swapping restores the firmware's crossed channels, so it must be loud."""
    once = sc.apply_swap_fix(_toy_df())
    with pytest.raises(ValueError, match="device-native"):
        sc.apply_swap_fix(once.copy())


def test_swap_fix_refuses_derived_csv_shape():
    """The published CSV keeps swap_corrected but drops powerC_raw/powerF_raw;
    the guard must still fire on that reduced schema."""
    slim = sc.apply_swap_fix(_toy_df()).drop(columns=["powerC_raw", "powerF_raw"])
    with pytest.raises(ValueError, match="device-native"):
        sc.apply_swap_fix(slim)


def test_add_corrected_columns_present_and_sane():
    df = sc.add_corrected_columns(sc.apply_swap_fix(_toy_df()))
    for col in ("comp_corrected", "flow_corrected"):
        assert col in df.columns
    assert df["comp_corrected"].dropna().between(0, 1).all()
    assert (df["flow_corrected"] >= 0).all()


def test_composition_only_computed_in_no_flow_window():
    """Composition is measured only when there is no flow: comp_corrected is NaN
    where the device reports flow (firmware flow >= 0.5) and valid otherwise."""
    df = sc.add_corrected_columns(sc.apply_swap_fix(_toy_df()))
    # toy row 0 has firmware flow=11 (flowing) -> no composition
    assert pd.isna(df["comp_corrected"].iloc[0])
    # toy row 1 has firmware flow=0 (no flow) -> composition computed
    assert not pd.isna(df["comp_corrected"].iloc[1])
    # flow_corrected is still produced for every row
    assert df["flow_corrected"].notna().all()


def test_composition_gated_on_corrected_flow_too():
    """The firmware gate alone is not enough. For 60 s after every entry into
    state 0 the firmware forces `flow` to exactly 0.0 regardless of the true
    value (main.cpp:979-985), so a row can read firmware-zero while the flow
    thermistor is plainly cooled by gas. The conc poly is far out of regime
    there -- it returns a clamped 1.0 -- so the recomputed flow gates it too."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-02-01T00:00Z"] * 2),
        "state": [0.0, 0.0], "temp": [25.0, 25.0],
        "flow": [0.0, 0.0],               # firmware says no flow on BOTH rows
        "comp": [0.2, 0.2],
        "powerC": [40.0, 25.0],           # device FLOW thermistor: cooled vs baseline
        "powerF": [26.0, 26.0],
    })
    out = sc.add_corrected_columns(sc.apply_swap_fix(df))
    assert out["flow_corrected"].iloc[0] >= sc.NO_FLOW_MAX_CORRECTED
    assert out["flow_corrected"].iloc[1] < sc.NO_FLOW_MAX_CORRECTED
    # gas was moving on row 0 despite the firmware zero -> no composition
    assert pd.isna(out["comp_corrected"].iloc[0])
    assert not pd.isna(out["comp_corrected"].iloc[1])


def test_add_corrected_columns_requires_firmware_flow():
    """`flow` is a gate, not an optional passenger: dropping it must not
    silently widen the no-flow window to every row."""
    slim = sc.apply_swap_fix(_toy_df()).drop(columns=["flow"])
    with pytest.raises(ValueError, match="flow"):
        sc.add_corrected_columns(slim)


def test_composition_restricted_to_valid_temperature_window():
    """comp_corrected is NaN outside the lab T-calibration window (TEMP_VALID),
    even at no-flow: the conc poly's T-extrapolation there is an artefact (an
    inverse parabola in T from the poly's T^2 term)."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-02-01T00:00Z"] * 3),
        "state": [0.0, 0.0, 0.0], "temp": [25.0, 40.0, 10.0],
        "flow": [0.0, 0.0, 0.0], "comp": [0.2, 0.2, 0.2],
        "powerC": [25.0, 25.0, 25.0], "powerF": [24.0, 24.0, 24.0],
    })
    out = sc.add_corrected_columns(sc.apply_swap_fix(df))
    lo, hi = sc.TEMP_VALID
    assert lo == 16.0 and hi == 33.0
    assert not pd.isna(out["comp_corrected"].iloc[0])   # 25 C, in window
    assert pd.isna(out["comp_corrected"].iloc[1])        # 40 C, above window
    assert pd.isna(out["comp_corrected"].iloc[2])        # 10 C, below window


def test_corrected_composition_uses_swapped_raw_powers():
    """In a no-flow row, comp_corrected = composition(pf=flow-therm, pc=comp-therm)
    on the RAW swap-fixed powers."""
    df = sc.add_corrected_columns(sc.apply_swap_fix(_toy_df()))
    src = _toy_df()
    expected1 = sc.composition(src["powerC"][1], src["powerF"][1], src["temp"][1])
    assert df["comp_corrected"][1] == pytest.approx(expected1, abs=1e-9)


# --------------------------------------------------------------------------
# Integration against real field data + Drager X-am 8000 reference
# --------------------------------------------------------------------------
@pytest.mark.skipif(not (RAW / "SN_01001").is_dir(), reason="field data not mounted")
def test_real_sn01001_composition_unwinds_inversion():
    """End-to-end: corrected no-flow CH4 lands near the reference 0.50 and far
    from the firmware-corrupted ~0.20."""
    fs = sorted(glob.glob(str(RAW / "SN_01001" / "*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df = sc.add_corrected_columns(sc.apply_swap_fix(df))
    m = df[(df.state == 0)].dropna(subset=["comp_corrected"])
    med = m["comp_corrected"].median()
    assert 0.30 <= med <= 0.58, med       # biogas band, near GT 0.497
    # the firmware comp column is inverted/wrong (~0.20); we must be clearly above it
    assert med > m["comp"].mean() + 0.1


@pytest.mark.skipif(not (RAW / "SN_01001").is_dir(), reason="field data not mounted")
def test_real_composition_never_published_while_gas_flows():
    """The published invariant, on real field data: no composition survives on a
    row whose recomputed flow says gas is moving. Before both gates were applied
    9% of all published composition values violated this, and every physically
    impossible value in the dataset (a clamped 1.0 CH4) lived among them."""
    fs = sorted(glob.glob(str(RAW / "SN_01001" / "*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df = sc.add_corrected_columns(sc.apply_swap_fix(df[df["state"] == 0]))
    published = df.dropna(subset=["comp_corrected"])
    assert len(published) > 1000, "expected a substantial composition population"
    assert (published["flow_corrected"] < sc.NO_FLOW_MAX_CORRECTED).all()
    # the clamp ceiling is unreachable for real biogas; hitting it means the poly
    # was extrapolated out of regime
    assert published["comp_corrected"].max() < 1.0


# --------------------------------------------------------------------------
# derive.py: the state filter is applied at ingest, not left to the caller
# --------------------------------------------------------------------------
def _raw_day_df():
    """One device-day as the firmware emits it: sensor rows in states 0, 1, 5
    and 6 interleaved with a null-state heartbeat row."""
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-02-01T00:00Z", "2026-02-01T00:01Z",
                                     "2026-02-01T00:02Z", "2026-02-01T00:03Z",
                                     "2026-02-01T00:04Z"]),
        "state": [1.0, 0.0, 5.0, np.nan, 6.0],
        "temp": [25.0, 25.0, 25.0, np.nan, 25.0],
        "pressure": [1.0, 1.0, 1.0, np.nan, 1.0],
        "flow": [0.0, 0.0, 0.0, np.nan, 0.0],
        "comp": [0.2, 0.2, 0.2, np.nan, 0.2],
        "powerC": [55.0, 27.0, 0.0, np.nan, 0.0],
        "powerF": [40.0, 24.0, 0.0, np.nan, 0.0],
        "battery_volt": [np.nan, np.nan, np.nan, 3.9, np.nan],
        "rssi_dbm": [np.nan, np.nan, np.nan, -80.0, np.nan],
    })


def test_derive_emits_only_state_zero_rows(tmp_path):
    import derive
    src = tmp_path / "2026-02-01.parquet"
    dst = tmp_path / "2026-02-01.csv"
    _raw_day_df().to_parquet(src)
    assert derive.convert_file(src, dst) == "ok"
    out = pd.read_csv(dst)
    assert list(out.columns) == derive.OUTPUT_COLUMNS
    assert len(out) == 1
    assert (out["state"] == 0).all()
    # the warm-up row (state 1) would have read ~7 L/min; it must not survive
    assert out["flow_corrected"].max() < 1.0


def test_derive_skips_day_without_state_zero_rows(tmp_path):
    import derive
    df = _raw_day_df()
    df = df[df["state"] != 0]
    src = tmp_path / "2026-02-02.parquet"
    dst = tmp_path / "2026-02-02.csv"
    df.to_parquet(src)
    status = derive.convert_file(src, dst)
    assert status.startswith("no-state-0")
    assert not dst.exists()


def test_derive_output_columns_exclude_corrupted_and_heap_fields():
    """The firmware flow/comp columns are corrupted by the swap and mem_free is
    heap telemetry; none of them belongs in the published schema."""
    import derive
    for col in ("flow", "comp", "mem_free"):
        assert col not in derive.OUTPUT_COLUMNS
    for col in ("battery_volt", "rssi_dbm"):
        assert col in derive.OUTPUT_COLUMNS
