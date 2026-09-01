# Code

Everything needed to turn `data/raw_data/` into the derived data products.
Analysis and figure code for the papers lives in separate repositories; see the
[root README](../README.md).

| file | purpose |
|---|---|
| `derive.py` | ingest: raw Parquet to derived CSVs; keeps state-0 rows only, then applies the correction |
| `sensor_correction.py` | the channel-swap fix and the corrected quantities |
| `test_sensor_correction.py` | unit and integration tests for the correction, plus the deposit-metadata drift check |

```bash
pip install -r ../requirements.txt
python3 derive.py                 # ~15 s -> data/derived_data/, 2715 files, 88 MB
python3 -m pytest test_sensor_correction.py
```

`derive.py` accepts `--raw`, `--out` and `--quiet`.

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
deployed polynomial and the King's-law fit carry their own temperature terms,
and the detrend coefficients used in earlier internal revisions had no traceable
provenance.

## Testing

25 tests: the polynomial and King's-law formulas against their documented forms,
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
