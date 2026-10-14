# Documentation

Method notes for the corrections and derived quantities in this dataset.

| file | contents |
|---|---|
| `field_correction.md` | the thermistor channel-swap fix: why it exists, how flow and composition are recomputed, validation against the reference analyser, and the limits of both; §7 the per-unit zero-flow calibration (`flow_zero_cal`); §8 the gap filling of sleeps during use (`gap_fill_L`) |

Read `field_correction.md` before using `flow_corrected`, `flow_zero_cal`, `gap_fill_L` or `comp_corrected` for
anything quantitative. It documents which validity limits the code enforces (the state filter, the
no-flow gate and the temperature window) and which it does not (the flow and
composition calibration envelopes), and the sense in which the flow scale is and
is not established.

Companion material lives with the data it describes:
[`../data/derived_data/README.md`](../data/derived_data/README.md) for the derived
schema and what each column can claim, and
[`../data/metadata/README.md`](../data/metadata/README.md) for the calibration
constants and their known limitations.
