# Raw telemetry

Per-device daily Parquet (SNAPPY), as emitted by the deployed firmware and
retrieved from the ThingsBoard backend. **This is the archival record. Nothing
here is corrected, filtered or reconstructed.**

```
raw_data/SN_XXXXX/YYYY-MM-DD.parquet
```

22 devices, 2,975 files, 5,499,810 rows, 207 MB, 2026-01-27 to 2026-07-19.
Per-device coverage in [`../metadata/devices.csv`](../metadata/devices.csv).
Nominal cadence 60 s; UTC throughout.

## Columns

| column | unit | description |
|---|---|---|
| `timestamp` | UTC | sample time (`timestamp[ms, tz=UTC]`) |
| `timestamp_ms` | ms | same instant, Unix epoch |
| `pressure` | mbar | digester head pressure, gauge |
| `state` | code | device state, see [`../metadata/states.csv`](../metadata/states.csv); codes 0, 1, 5 and 6 occur |
| `temp` | °C | sensor-body temperature |
| `flow` | L/min | firmware flow, **corrupted, see below** |
| `comp` | mole fraction | firmware composition, **inverted, see below** |
| `powerC` | mW | thermistor power, channel C |
| `powerF` | mW | thermistor power, channel F |
| `mem_free` | kB | MCU free heap |
| `rssi_dbm` | dBm | cellular signal strength |
| `battery_volt` | V | battery terminal voltage |
| `msg` | text | device log events, 184 files only |

## ⚠ The column names assert the wrong physics

The deployed firmware crossed the two thermistor ADC channels. In these files:

- **`powerC` holds the FLOW thermistor**, and
- **`powerF` holds the COMP thermistor**, the opposite of what the names say.

Consequently `flow` is magnitude-wrong and `comp` is inverted. There was no
over-the-air update path, so this is corrected in post-processing, not in
firmware.

**Do not use `flow` or `comp` at face value, and do not assume `powerC`/`powerF`
mean what they are called.** Run [`../../src/derive.py`](../../src/derive.py),
which keeps only state-0 rows, undoes the swap and recomputes both quantities;
method and validation in
[`../../docs/field_correction.md`](../../docs/field_correction.md).

## Schema is not stable across files

There are 16 distinct column sets across the fleet, and every device has between
2 and 8 of them. Some files carry `msg`, some lack `temp`, and column order
varies. Any loader concatenating these must reindex to an explicit column list.
`derive.py` does, which is why its output schema is uniform.

147 files are housekeeping-only: connectivity and battery heartbeats with no
sensor payload at all. All of them belong to `SN_01018`, which returned 157 files
in total but produced sensor data on only 10 days; treat it as a failed
deployment rather than a low-use household. `derive.py` reports these as skipped
rather than dropping them silently.

## Missingness is structural

About 29% of rows have every sensor column null. These are the slower-cadence
connectivity and battery heartbeats, and there was never a measurement on those
rows. `pressure`, `state`, `temp`, `powerC` and `powerF` are null on exactly the
same rows. Treat this as two interleaved streams on one timeline, not as data
loss.

## No personal data

These files contain no GPS, no identifiers and no household attributes, only
device telemetry. The device serial is the join key to
[`../survey.csv`](../survey.csv); see that file's notes on linkage risk.
