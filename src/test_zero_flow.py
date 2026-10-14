"""Tests for the per-unit zero-flow calibration (zero_flow.py)."""
import numpy as np
import pandas as pd
import pytest

import sensor_correction as sc
import zero_flow as zf

A, B = sc.LAB_KINGS["a"], sc.LAB_KINGS["b"]
P5 = A + B * np.sqrt(5.0)                      # power that reads 5 L/min


def _fit(p0=23.46, slope=0.1, t_lo=18.0, t_hi=35.0, status="ok"):
    return dict(sensor="SN_X", n_rows=1000, p0_ref=p0, slope=slope,
                t_lo=t_lo, t_hi=t_hi, resid_sd=0.3, status=status)


# --------------------------------------------------------------------------
# Shift and flow
# --------------------------------------------------------------------------
def test_no_flow_threshold_is_half_litre_per_minute():
    assert zf.P_NO_FLOW_MAX == pytest.approx(A + B * np.sqrt(0.5))
    assert sc.kings_flow(zf.P_NO_FLOW_MAX) == pytest.approx(0.5)


def test_zero_shift_of_fleet_unit_leaves_flow_unchanged():
    t = np.array([25.0])
    s = zf.zero_shift(t, _fit(p0=23.46), fleet_p0=23.46)
    assert s == pytest.approx([0.0])
    assert zf.flow_zero_cal(np.array([P5]), s) == pytest.approx(sc.kings_flow(np.array([P5])))


def test_one_mW_high_zero_reads_lower_flow():
    s = zf.zero_shift(np.array([25.0]), _fit(p0=24.46), fleet_p0=23.46)
    q = zf.flow_zero_cal(np.array([P5]), s)[0]
    assert q == pytest.approx(((P5 - 1.0 - A) / B) ** 2)      # about 4.35 L/min
    assert q < 5.0


def test_power_at_unit_zero_maps_to_zero_flow():
    s = zf.zero_shift(np.array([25.0]), _fit(p0=24.46), fleet_p0=23.46)
    assert zf.flow_zero_cal(np.array([A + 1.0]), s)[0] == pytest.approx(0.0)


def test_shift_follows_temperature_slope():
    s = zf.zero_shift(np.array([20.0, 30.0]), _fit(p0=23.46, slope=0.1), fleet_p0=23.46)
    assert s == pytest.approx([-0.5, 0.5])


def test_temperature_clipped_to_fit_range():
    f = _fit(slope=0.1, t_lo=18.0, t_hi=35.0)
    s = zf.zero_shift(np.array([40.0, 10.0]), f, fleet_p0=23.46)
    s_edge = zf.zero_shift(np.array([35.0, 18.0]), f, fleet_p0=23.46)
    assert s == pytest.approx(s_edge)


def test_nan_temp_uses_constant_zero():
    s = zf.zero_shift(np.array([np.nan]), _fit(p0=24.46, slope=0.1), fleet_p0=23.46)
    assert s == pytest.approx([1.0])


def test_unit_without_fit_falls_back_to_flow_corrected():
    f = _fit(p0=np.nan, slope=np.nan, status="too-few-rows")
    s = zf.zero_shift(np.array([25.0, np.nan]), f, fleet_p0=23.46)
    assert s == pytest.approx([0.0, 0.0])


def test_zero_shift_uses_weekly_p0_where_given():
    s = zf.zero_shift(np.array([25.0, 25.0, np.nan]), _fit(p0=23.46, slope=0.1), fleet_p0=23.46,
                      p0_row=np.array([24.46, np.nan, 24.46]))
    assert s == pytest.approx([1.0, 0.0, 1.0])


def test_flow_zero_cal_monotonic_in_power_across_handover():
    p = np.linspace(20, 80, 601)
    q = zf.flow_zero_cal(p, np.full_like(p, 0.7))
    assert np.all(np.diff(q) >= -0.05)          # handover micro-step is about 0.04 L/min


# --------------------------------------------------------------------------
# Fitting the zero
# --------------------------------------------------------------------------
def test_fit_zero_recovers_synthetic_line():
    rng = np.random.default_rng(0)
    t = rng.uniform(17, 35, 2000)
    p = 23.5 + 0.1 * (t - 25) + rng.normal(0, 0.3, t.size)
    f = zf.fit_zero(pd.DataFrame({"temp": t, "powerF": p}), "SN_X")
    assert f["status"] == "ok"
    assert f["p0_ref"] == pytest.approx(23.5, abs=0.05)
    assert f["slope"] == pytest.approx(0.1, abs=0.01)
    assert f["t_lo"] == pytest.approx(np.percentile(t, 5))
    assert set(f) == set(zf.ZERO_TABLE_COLUMNS)


def test_fit_zero_with_given_slope_fits_intercept_only():
    t = np.linspace(17, 35, 1000)
    p = 23.5 + 0.3 * (t - 25)
    f = zf.fit_zero(pd.DataFrame({"temp": t, "powerF": p}), "SN_X", slope=0.1)
    assert f["slope"] == 0.1
    assert f["p0_ref"] == pytest.approx(np.median(p - 0.1 * (t - 25)))


def test_fit_zero_too_few_rows_status():
    f = zf.fit_zero(pd.DataFrame({"temp": [25.0] * 10, "powerF": [23.0] * 10}), "SN_X")
    assert f["status"] == "too-few-rows"
    assert np.isnan(f["p0_ref"])


def test_week_key_is_monday_of_the_utc_week():
    ts = pd.Series(pd.to_datetime(["2026-03-01T23:30Z", "2026-03-02T00:10Z", "2026-03-08T12:00Z"]))
    assert zf.week_key(ts).tolist() == ["2026-02-23", "2026-03-02", "2026-03-02"]


def _weeks(p0_by_week, n_per_week):
    rows = []
    for i, (p0, n) in enumerate(zip(p0_by_week, n_per_week)):
        t0 = pd.Timestamp("2026-03-02", tz="UTC") + pd.Timedelta(weeks=i)
        temp = np.linspace(18, 32, n)
        rows.append(pd.DataFrame({"timestamp": t0 + pd.to_timedelta(np.arange(n), unit="min"),
                                  "temp": temp, "powerF": p0 + 0.1 * (temp - 25)}))
    return pd.concat(rows, ignore_index=True)


def test_fit_weekly_zero_tracks_step_drift():
    w = zf.fit_weekly_zero(_weeks([23.0, 25.0], [300, 300]), slope=0.1)
    assert w.to_dict() == pytest.approx({"2026-03-02": 23.0, "2026-03-09": 25.0})


def test_fit_weekly_zero_drops_sparse_weeks():
    w = zf.fit_weekly_zero(_weeks([23.0, 25.0], [300, zf.N_MIN_WEEK - 1]), slope=0.1)
    assert list(w.index) == ["2026-03-02"]


# --------------------------------------------------------------------------
# Selecting no-flow rows
# --------------------------------------------------------------------------
def _timeline(minutes, power, flow):
    return pd.DataFrame({
        "timestamp": pd.Timestamp("2026-03-01", tz="UTC") + pd.to_timedelta(minutes, unit="min"),
        "temp": 25.0, "powerF": power, "flow_corrected": flow})


def test_select_no_flow_excludes_post_cooking_window():
    df = _timeline([0, 1, 2, 10, zf.POST_FLOW_EXCLUDE_MIN + 5],
                   [P5, P5, P5, 23.4, 23.4], [5.0, 5.0, 5.0, 0.0, 0.0])
    out = zf.select_no_flow(df)
    assert len(out) == 1
    assert out["timestamp"].iloc[0] == df["timestamp"].iloc[-1]


def test_select_no_flow_keeps_rows_before_any_cooking():
    df = _timeline([0, 1], [23.4, 23.4], [0.0, 0.0])
    assert len(zf.select_no_flow(df)) == 2


def test_select_no_flow_excludes_flowing_power_and_nan():
    df = _timeline([100, 101, 102], [23.4, 30.0, 23.4], [0.0, 0.9, 0.0])
    df.loc[2, "temp"] = np.nan
    out = zf.select_no_flow(df)
    assert len(out) == 1 and out["powerF"].iloc[0] == 23.4


def test_select_no_flow_ignores_row_order():
    df = _timeline([0, 1, 2, 10, zf.POST_FLOW_EXCLUDE_MIN + 5],
                   [P5, P5, P5, 23.4, 23.4], [5.0, 5.0, 5.0, 0.0, 0.0])
    assert len(zf.select_no_flow(df.iloc[::-1])) == 1
