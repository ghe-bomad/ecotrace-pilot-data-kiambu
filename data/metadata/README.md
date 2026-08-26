# Metadata

Supporting tables and constants. Small, hand-maintained or generated once.

## `devices.csv`

One row per device: coverage window, day counts and row counts.

| column | description |
|---|---|
| `sensor` | `SN_XXXXX`, matches the `raw_data/` directory name |
| `first_date`, `last_date` | coverage window (UTC dates) |
| `n_days` | Parquet files present |
| `n_sensor_days` | of those, files actually carrying sensor payload |
| `n_rows` | total telemetry rows |

`n_days` and `n_sensor_days` differ only for **SN_01018** (157 versus 10): that
unit returned connectivity and battery heartbeats for almost the whole pilot but
produced sensor data on 10 days only. Treat it as a failed deployment, not as a
low-use household.

Coverage is uneven by design, because units were installed and removed on
different dates. `SN_01012` (19 days) and `SN_01023` (64 days) are short;
`SN_01005` and `SN_01010` end 2026-05-07. `SN_01022` does not exist.

## `states.csv`

Device state codes, referenced by the `state` column in both raw and derived
data. Transcribed from the deployed firmware (`config.h`, `main.cpp`), with a
`firmware_ref` column citing the defining line for each state, so every claim
here is checkable against the source.

| column | description |
|---|---|
| `code` | numeric value appearing in the `state` column |
| `name` | firmware state name |
| `flow_comp_valid` | whether flow and composition carry information in this state |
| `thermistors` | what the heaters are doing |
| `observed_in_pilot` | whether the code actually occurs in this dataset |
| `description` | behaviour, entry and exit conditions, named firmware constants |
| `led_pattern` | field diagnostic |
| `firmware_ref` | `file:line` in the firmware repository |

Codes **0, 1, 5 and 6** occur in this dataset. Low voltage (6) is rare: 866
rows on three units (SN_01004: 291, SN_01012: 473, SN_01013: 102). States 2, 3
and 4 are defined by the firmware but never entered.

### State 0 is the only usable state

`derive.py` keeps only `state == 0` rows, so the derived CSVs carry nothing
else. **If you read the raw Parquet directly, apply that filter yourself before
any quantitative use.** States 1, 5 and 6 produce plausible-looking but
meaningless derived values. During warm-up (state 1) both thermistors are
driven hard to reach setpoint, and the King's-law inverse reads that power as
flow (a median of 0.04 L/min, but 27 L/min at the 90th percentile and 53 at
the 99th) on rows where the firmware itself correctly reports zero; unfiltered,
56% of the integrated volume sits in state 1. Sleep (state 5) is the most
common state in the dataset; both thermistors are at zero power there, and the
composition polynomial clamps to exactly 0.

### Two firmware behaviours that shape the data

**A 60 s settling window.** For `STABILIZATION_TIME_MS = 60 s` after entering
state 0, the firmware forces `flow` and `comp` to exactly 0.0 regardless of the
true value (`main.cpp:979-985`). The device duty-cycles (sleep 220 s, warm-up
50 s, then normal), so a settling window follows every wake, not just boot. Rows
reading exactly zero early in a state-0 run are artefacts, not measurements.

**A 2 L/min flow deadband.** The firmware reports any flow below 2 L/min as
exactly 0 (`main.cpp:982`). Verified in the data: of 261,556 sampled firmware
`flow` values, **0.0% fall in the open interval (0, 2)**. 86.8% are exactly zero
and 13.2% are 2 or above.

That deadband has a useful consequence. `NO_FLOW_MAX = 0.5` in
`sensor_correction.py`, which gates `comp_corrected`, has no documented source.
But since firmware flow can never lie between 0 and 2, *any* threshold in
(0, 2.0) selects exactly the same rows. The constant is arbitrary within a wide
equivalence class, and its real meaning is simply "the firmware reports no flow."

## `calibration_card.json`

The calibration constants used by
[`../../src/sensor_correction.py`](../../src/sensor_correction.py), vendored
from the CFD and lab calibration work so this repository is self-contained.
Identical to its source except for the `meta.source_repo` field, which was
rewritten to describe the sensor paper's repository instead of a local path.

Contains the deployed 10-term polynomials, the lab and CFD King's-law fits, the
lab calibration envelope, and composition-calibration parameters. The correction
code transcribes constants from it rather than reading it at runtime, so the file
is provenance rather than a live input, but the transcription is verified exact
for every coefficient.

Two things the card records that the code does **not** enforce, and re-users
should know:

- `lab_cal_envelope` is `Q ∈ [4, 12] L/min`, `T ∈ [16, 33] °C`,
  `X_CH₄ ∈ [0.3, 0.7]`. Only the temperature bound is applied (to
  `comp_corrected`); flow and composition are emitted outside the rest.
- The `deployed_polynomial.flow` entry is deliberately **not** used. It is
  unphysical, having no zero and returning about 11 L/min at 25 °C with both
  powers at 0, and it reproduces the firmware's own corrupted `flow` at
  r ≈ +0.97. Flow comes from the King's-law inverse instead. The `conc` entry
  **is** used and is the only path to `comp_corrected`.

Two limitations of the card's own numbers are worth stating plainly. The
King's-law exponent `n = 0.5` was assumed rather than fitted. And the reported
R² = 0.9992 is two parameters against three group means; against the 540
underlying raw bench rows it is R² = 0.783, RMSE 2.10 mW. See
[`../derived_data/README.md`](../derived_data/README.md) for what the resulting
flow column can and cannot claim.
