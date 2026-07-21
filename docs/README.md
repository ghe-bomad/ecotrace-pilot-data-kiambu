# Documentation

Method notes for the corrections and derived quantities in this dataset.

| file | contents |
|---|---|
| `field_correction.md` | the thermistor channel-swap fix: why it exists, how flow and composition are recomputed, validation against the reference analyser, and the limits of both |

Read `field_correction.md` before using `flow_corrected` or `comp_corrected` for
anything quantitative. It documents the validity windows that the code does not
enforce, and the sense in which the flow scale is and is not established.

Companion material lives with the data it describes:
[`../data/derived_data/README.md`](../data/derived_data/README.md) for the derived
schema and what each column can claim, and
[`../data/metadata/README.md`](../data/metadata/README.md) for the calibration
constants and their known limitations.
