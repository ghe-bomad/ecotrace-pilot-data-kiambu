# Code

Everything needed to turn `data/raw_data/` into the derived data products.
Analysis and figure code for the papers lives in separate repositories; see the
[root README](../README.md).

| file | purpose |
|---|---|
| `derive.py` | ingest: raw Parquet to derived CSVs; keeps state-0 rows only, then applies the correction |
| `sensor_correction.py` | the channel-swap fix and the corrected quantities |
| `zero_flow.py` | per-unit zero-flow calibration: fits each unit's no-flow power and fills `flow_zero_cal` |
| `gap_fill.py` | fills `gap_fill_L`, the unmeasured gas next to rows with gas in use, and its holdout check |
| `test_sensor_correction.py` | unit and integration tests for the correction, plus the deposit-metadata drift check |
| `test_zero_flow.py` | tests for the zero-flow calibration |
| `test_gap_fill.py` | tests for the gap filling |

```bash
pip install -r ../requirements.txt
python3 derive.py                 # ~30 s -> data/derived_data/, 2715 files, 105 MB
python3 -m pytest
```

`derive.py` accepts `--raw`, `--out`, `--quiet`, `--zero-slope` (one temperature
slope for every unit's zero instead of a per-unit fit) and `--gap-fill-check`
(the holdout test of the gap filling on existing derived data).

## `sensor_correction.py`

The deployed firmware crossed the two thermistor ADC channels, so the device's
`powerC` carries the FLOW thermistor and `powerF` the COMP thermistor. No
over-the-air update path existed, so this is corrected in post-processing.

- `apply_swap_fix(df)` swaps the two power columns so `powerF` is the flow
  thermistor and `powerC` the comp thermistor, and sets `swap_corrected`.
- `add_corrected_columns(df)` adds `comp_corrected` (CH₄ mole fraction, from
  the deployed conc polynomial) and `flow_corrected` (L/min, King's-law inverse
  on the flow-thermistor power). `comp_corrected` is gated on **both** flow
  indicators, the firmware's `flow` and the recomputed `flow_corrected`: the
  firmware zeroes its own column for 60 s after every wake, so it cannot gate
  alone. See [`../docs/field_correction.md`](../docs/field_correction.md) §3.
- `composition()` and `kings_flow()` are the pure functions, scalar- and
  array-friendly, usable independently of pandas.

**`apply_swap_fix` raises on an already-corrected frame** rather than returning
it unchanged. Applying the swap twice restores the firmware's crossed
assignment; the result is physically wrong but entirely plausible-looking, with
nothing to signal the error. The `swap_corrected` flag in the derived CSVs is
what makes that guard work, so it is not decorative. Derive from Parquet, never
from a derived CSV.

Constants are transcribed from
[`../data/metadata/calibration_card.json`](../data/metadata/calibration_card.json),
verified exact for every coefficient.

## Deliberate omissions

The deployed 10-term **flow** polynomial is not implemented. It is unphysical:
`flow_poly(0, 0, 25 °C)` returns about 11 L/min with both thermistors at zero
power, and it reproduces the firmware's own corrupted `flow` column at
r ≈ +0.97, so it was a re-derivation of the corrupted signal rather than an
independent estimate. The **conc** polynomial is retained and is the only path
to `comp_corrected`.

No temperature-detrended `powerC_adj` / `powerF_adj` columns are produced. The
deployed polynomial carries its own temperature terms, the King's-law zero's
temperature dependence is fitted per unit by `zero_flow.py`, and the detrend
coefficients used in earlier internal revisions had no traceable provenance.

## `zero_flow.py`

`flow_corrected` uses one lab King's-law offset for every unit. `zero_flow.py`
fits each unit's no-flow power from its own state-0 rows (a temperature slope
per unit, an intercept per week) and writes `flow_zero_cal`, the same inverse
on the power shifted by the unit's offset from the fleet zero.
`add_zero_cal()` runs over the whole derived directory after `derive.py` has
written it, re-reading each CSV with `float_precision="round_trip"` so the
other columns come back exactly as written. See
[`../docs/field_correction.md`](../docs/field_correction.md) §7.

## `gap_fill.py`

Units sleep between measurements and often fell asleep while gas was still
flowing, because the firmware's stay-awake decision used the crossed channels.
`gap_fill.py` lets every row with gas in use stand in for half of the unmeasured
time on each side of it, at its own flow, up to one sleep cycle per side, and
writes the litres to `gap_fill_L` on that row. `add_gap_fill()` runs over all of
a unit's files at once, so gaps across midnight are handled, and writes
`gap_fill_summary.csv`.
`holdout_check()` tests the rule on continuous measuring runs. See
[`../docs/field_correction.md`](../docs/field_correction.md) §8.

## Testing

`test_gap_fill.py` (16 tests) covers the rule for sleeps, starts, ends, long
gaps and cycling through use, the holdout check, gaps across file boundaries,
the unit summary, idempotency, and an end-to-end run of `derive.py`.

`test_zero_flow.py` (26 tests) covers the zero shift and its fallbacks, the
no-flow row selection, the fits, and the pass over a derived directory,
including that it is idempotent and leaves every other column byte-identical.

`test_sensor_correction.py` has 27 tests: the polynomial and King's-law formulas against their documented forms,
the swap fix and its guard (including on the reduced published schema), both
validity gates on `comp_corrected`, the state filter and fixed output schema of
`derive.py`, and integration checks against real field data.
`test_deployed_flow_polynomial_is_gone` asserts the removed polynomial stays
removed, and `test_real_composition_never_published_while_gas_flows` asserts the
published no-composition-during-flow invariant on real data.

One test is not about the correction at all.
`test_zenodo_json_agrees_with_citation_cff` keeps `.zenodo.json` in step with
`CITATION.cff`. That file exists only because Zenodo's GitHub integration types
every release as *software* and ignores `CITATION.cff`'s `type:` field, so a
dataset deposit needs the override — but when `.zenodo.json` is present Zenodo
reads it *instead of* `CITATION.cff`, meaning it must repeat the whole author
list. The test is what stops the two copies diverging. It skips if `pyyaml` is
not installed, which is why `pyyaml` is not in `requirements.txt`: the
derivation does not need it.
