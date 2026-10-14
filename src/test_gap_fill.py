"""Tests for gap filling of sleeps during use (gap_fill.py)."""
import numpy as np
import pandas as pd
import pytest

import gap_fill as gf


def _rows(seconds, flow, t0="2026-03-01T12:00Z"):
    return pd.DataFrame({
        "timestamp": pd.Timestamp(t0) + pd.to_timedelta(seconds, unit="s"),
        "flow_zero_cal": flow})


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------
def test_fills_a_sleep_between_two_steady_flowing_rows():
    # 300 s apart; each row stands for its own minute, so 240 s are unmeasured
    out = gf.gap_fill_litres(_rows([0, 300], [6.0, 6.6]))
    assert out.tolist() == pytest.approx([0.0, (6.0 + 6.6) / 2 * 4])


def test_no_fill_when_flow_changes_more_than_the_tolerance():
    out = gf.gap_fill_litres(_rows([0, 300], [6.0, 6.0 * (1 + gf.MAX_REL_CHANGE) + 0.1]))
    assert out.tolist() == [0.0, 0.0]


def test_fills_at_exactly_the_tolerance():
    out = gf.gap_fill_litres(_rows([0, 300], [5.0, 5.0 * (1 + gf.MAX_REL_CHANGE)]))
    assert out.iloc[1] > 0


def test_no_fill_at_the_start_of_use():
    # quiet before the sleep: the start time of use is unknown, so nothing is filled
    out = gf.gap_fill_litres(_rows([0, 300], [0.0, 6.0]))
    assert out.tolist() == [0.0, 0.0]


def test_no_fill_below_the_flowing_threshold():
    out = gf.gap_fill_litres(_rows([0, 300], [1.9, 1.9]))
    assert out.tolist() == [0.0, 0.0]


def test_no_fill_for_continuous_rows_or_data_gaps():
    # 60 s apart (measured), and a 3 h data gap (not one sleep cycle)
    out = gf.gap_fill_litres(_rows([0, 60, 60 + 3 * 3600], [6.0, 6.0, 6.0]))
    assert out.tolist() == [0.0, 0.0, 0.0]


def test_gap_bounds():
    lo, hi = gf.SLEEP_GAP_S
    out = gf.gap_fill_litres(_rows([0, lo, lo + hi, lo + hi + hi + 1], [6.0, 6.0, 6.0, 6.0]))
    # lo itself is excluded, hi is included, hi + 1 is excluded
    assert out.tolist() == pytest.approx([0.0, 0.0, 6.0 * (hi - 60) / 60, 0.0])


def test_no_fill_next_to_missing_flow():
    out = gf.gap_fill_litres(_rows([0, 300, 600], [np.nan, 6.0, 6.0]))
    assert out.tolist() == pytest.approx([0.0, 0.0, 6.0 * 4])


def test_consecutive_sleeps_during_use_are_each_filled():
    out = gf.gap_fill_litres(_rows([0, 300, 600], [6.0, 6.0, 6.0]))
    assert out.tolist() == pytest.approx([0.0, 24.0, 24.0])


def test_order_of_rows_does_not_matter():
    df = _rows([0, 300, 600], [6.0, 6.3, 6.0])
    a = gf.gap_fill_litres(df)
    b = gf.gap_fill_litres(df.iloc[::-1]).reindex(df.index)
    pd.testing.assert_series_equal(a, b)


# --------------------------------------------------------------------------
# Holdout check
# --------------------------------------------------------------------------
def test_holdout_check_is_unbiased_on_steady_flow():
    rng = np.random.default_rng(0)
    q = 6.0 + rng.normal(0, 0.1, 200)
    res = gf.holdout_check(_rows(np.arange(200) * 60, q))
    assert res["n_windows"] > 100
    assert res["bias"] == pytest.approx(0.0, abs=0.01)


# --------------------------------------------------------------------------
# The pass over a derived-data directory
# --------------------------------------------------------------------------
def _write_two_days(root, sn="SN_A"):
    d = root / sn
    d.mkdir(parents=True)
    cols = ["timestamp", "temp", "powerF", "flow_corrected", "flow_zero_cal", "gap_fill_L"]
    day1 = pd.DataFrame({"timestamp": pd.to_datetime(["2026-03-01T23:54:00Z", "2026-03-01T23:58:00Z"]),
                         "temp": 25.0, "powerF": 40.0, "flow_corrected": 6.0,
                         "flow_zero_cal": [0.0, 6.0], "gap_fill_L": np.nan})[cols]
    day2 = pd.DataFrame({"timestamp": pd.to_datetime(["2026-03-02T00:03:00Z", "2026-03-02T00:04:00Z"]),
                         "temp": 25.0, "powerF": 40.0, "flow_corrected": 6.0,
                         "flow_zero_cal": [6.0, 6.0], "gap_fill_L": np.nan})[cols]
    day1.to_csv(d / "2026-03-01.csv", index=False)
    day2.to_csv(d / "2026-03-02.csv", index=False)


def test_add_gap_fill_crosses_file_boundaries(tmp_path):
    _write_two_days(tmp_path)
    gf.add_gap_fill(tmp_path)
    d1 = pd.read_csv(tmp_path / "SN_A" / "2026-03-01.csv")
    d2 = pd.read_csv(tmp_path / "SN_A" / "2026-03-02.csv")
    assert d1["gap_fill_L"].tolist() == [0.0, 0.0]
    # the sleep from 23:58 to 00:03 is filled on the first row after it
    assert d2["gap_fill_L"].tolist() == pytest.approx([6.0 * 4, 0.0])


def test_add_gap_fill_writes_unit_summary(tmp_path):
    _write_two_days(tmp_path)
    s = gf.add_gap_fill(tmp_path)
    t = pd.read_csv(tmp_path / "gap_fill_summary.csv")
    assert list(t.columns) == gf.SUMMARY_COLUMNS
    row = t.iloc[0]
    assert row["sensor"] == "SN_A"
    assert row["n_sleeps_during_use"] == 1 and row["n_filled"] == 1
    assert row["filled_L"] == pytest.approx(24.0)
    assert row["measured_L"] == pytest.approx(18.0)
    assert s.equals(t)


def test_add_gap_fill_is_idempotent_and_leaves_other_columns_alone(tmp_path):
    _write_two_days(tmp_path)
    f = tmp_path / "SN_A" / "2026-03-02.csv"
    before = pd.read_csv(f, dtype=str)
    gf.add_gap_fill(tmp_path)
    first = pd.read_csv(f, dtype=str)
    gf.add_gap_fill(tmp_path)
    second = pd.read_csv(f, dtype=str)
    pd.testing.assert_frame_equal(first, second)
    cols = [c for c in before.columns if c != "gap_fill_L"]
    pd.testing.assert_frame_equal(before[cols], first[cols])


def test_derive_main_fills_gap_fill_column(tmp_path, monkeypatch):
    """End to end: derive.py's main() writes gap_fill_L and the summary table."""
    import sys
    import derive
    raw = tmp_path / "raw" / "SN_A"
    raw.mkdir(parents=True)
    t = pd.to_datetime(["2026-03-01T12:00Z", "2026-03-01T12:05Z"])
    pd.DataFrame({"timestamp": t, "state": [0.0, 0.0], "temp": 25.0, "pressure": 50.0,
                  "flow": [5.0, 5.0], "comp": 0.5,
                  # device-native: powerC carries the FLOW thermistor (~6 L/min)
                  "powerC": [38.7, 38.7], "powerF": [23.0, 23.0]}).to_parquet(raw / "2026-03-01.parquet")
    out = tmp_path / "derived"
    monkeypatch.setattr(sys, "argv", ["derive.py", "--raw", str(tmp_path / "raw"), "--out", str(out), "--quiet"])
    assert derive.main() == 0
    d = pd.read_csv(out / "SN_A" / "2026-03-01.csv")
    assert d["gap_fill_L"].iloc[0] == 0.0 and d["gap_fill_L"].iloc[1] > 0
    assert (out / "gap_fill_summary.csv").exists()
