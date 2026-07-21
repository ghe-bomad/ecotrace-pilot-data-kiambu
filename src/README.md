# Code

Everything needed to turn `data/raw_data/` into the derived data products.
Analysis and figure code for the papers lives in separate repositories; see the
[root README](../README.md).

| file | purpose |
|---|---|
| `derive.py` | ingest: raw Parquet to derived CSVs |
| `sensor_correction.py` | the channel-swap fix and the corrected quantities |
| `prepare_survey.py` | private survey export to published de-identified CSV |
| `test_sensor_correction.py` | unit and integration tests for the correction |

```bash
pip install -r ../requirements.txt
python3 derive.py                 # ~40 s -> data/derived_data/, 2828 files
python3 -m pytest test_sensor_correction.py
```

`derive.py` accepts `--raw`, `--out` and `--quiet`. `prepare_survey.py` requires
`--raw` pointing at the private export, which is not in this repository.

## `sensor_correction.py`

The deployed firmware crossed the two thermistor ADC channels, so the device's
`powerC` carries the FLOW thermistor and `powerF` the COMP thermistor. No
over-the-air update path existed, so this is corrected in post-processing.

- `apply_swap_fix(df)` swaps the two power columns so `powerF` is the flow
  thermistor and `powerC` the comp thermistor, and sets `swap_corrected`.
- `add_corrected_columns(df)` adds `comp_corrected` (CH₄ mole fraction, from
  the deployed conc polynomial) and `flow_corrected` (L/min, King's-law inverse
  on the flow-thermistor power).
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

The temperature-detrended `powerC_adj` and `powerF_adj` columns are also gone.
They were exactly recomputable from the retained columns (max error 7e-15) and
their detrend coefficients had no traceable provenance.

## Testing

19 tests: the polynomial and King's-law formulas against their documented forms,
the swap fix and its guard (including on the reduced published schema), the
validity masks on `comp_corrected`, and an integration check against real field
data. `test_deployed_flow_polynomial_is_gone` asserts the removed polynomial
stays removed.
