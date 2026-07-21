# Data

All data from the ecoTrace biogas-sensor pilot, Kiambu County, Kenya,
2026-01-27 to 2026-07-19. 22 devices, 2,975 device-days, 5,499,810 telemetry rows.

| path | what | tracked |
|---|---|---|
| [`raw_data/`](raw_data/) | per-device daily telemetry, Parquet | yes, 207 MB |
| [`derived_data/`](derived_data/) | swap-corrected flow and composition, CSV | no, regenerated |
| [`metadata/`](metadata/) | device inventory, state codes, calibration card | yes |
| `biogas_composition.json` | reference gas-composition spot measurements | yes |
| `survey.csv` | de-identified household survey, 20 households | yes |

Raw is the archival artifact; everything else is either derived from it
(`derived_data/`, via `src/derive.py`) or supporting context.

## `biogas_composition.json`

Spot measurements of digester gas composition taken with a Dräger X-am 8000
reference analyser, used to validate `comp_corrected`. Structure is
`{"01001": [{...}, ...], ...}`, mapping device number to a list of measurements:

| field | unit | notes |
|---|---|---|
| `date` | ISO-8601 UTC | measurement time |
| `CO2_vol_perc`, `CH4_vol_perc`, `O2_vol_perc` | vol-% | |
| `H2S_ppm`, `CO_ppm`, `NH3_ppm` | ppm | |
| `H2S_removal_available`, `after_H2S_removal` | `"True"`/`"False"` | strings, not booleans |
| `notes` | free text | digester type, desulfurizer condition |

**Two parsing traps.** `H2S_ppm` and `NH3_ppm` carry the string sentinel
`"max"` where the reading saturated, so the column is mixed-type; coerce
explicitly rather than letting a loader infer. And the booleans are quoted
strings. Keys are bare device numbers (`"01001"`), not `SN_` prefixed.

## `survey.csv`

20 households, 17 variables, one row per household, joined to telemetry by
`sensor`. Built from a private KoboToolbox export by
[`../src/prepare_survey.py`](../src/prepare_survey.py); the raw export is never
committed.

| column | type | description |
|---|---|---|
| `sensor` | `SN_XXXXX` | join key to `raw_data/` |
| `digester_m3` | int | nominal digester size |
| `age` | int | respondent age, **exact** |
| `gender_male` | 0/1 | |
| `household_size` | int | people resident |
| `n_dairy`, `n_non_dairy`, `n_goats`, `n_sheep` | float | livestock counts |
| `lsu` | float | feed-equivalent livestock units, `0.70·cattle + 0.10·(goats+sheep)`; pigs and chickens excluded because their manure is not fed to these digesters |
| `income` | 1 to 5 | monthly household income band, increasing |
| `affordability` | 1 to 4 | perceived affordability, increasing |
| `feeding_freq` | days/week | dung collection frequency, bucket midpoints |
| `feeds_per_day` | count/day | feeding events |
| `dung_fed_frac` | 0 to 1 | fraction of available dung fed |
| `fuel_replacement` | 1 to 5 | extent biogas replaced other fuels |
| `experience` | 1 to 4 | self-rated experience, increasing |

### De-identification, and what it does not achieve

Removed: GPS fixes (sub-metre precision, 20 distinct dwellings), both phone
numbers, Kobo record keys (`_id`, `_uuid`, `_index`), the enumerator account,
and all submission timestamps. Free-text responses are not carried through.
They were reviewed and contained no personal names, place names or numbers, but
are excluded on principle. Selection is by whitelist in `prepare_survey.py`, so
a new column in a future export cannot leak in by default.

**Residual risk is real and not eliminated.** At n = 20, k-anonymity is
unachievable for any useful quasi-identifier set. Exact `age` alone leaves 13 of
20 households unique; adding gender, education, occupation and household size
leaves 18 of 20. The `sensor` column further links each household to six months
of minute-resolution telemetry, from which daily routine and absence are legible.
Ethics approval (ETH Zurich 25 ETHICS-267; NACOSTI/P/25/4181252; SPU/555/2025)
covers release of anonymized data with written informed consent from every
household. Re-users must not attempt re-identification.

`prepare_survey.py --age-bands` coarsens age to 10-year bands, which takes
uniqueness on that variable from 13/20 to 1/20. It is the single highest-value
further mitigation available and still does not reach k ≥ 5.
