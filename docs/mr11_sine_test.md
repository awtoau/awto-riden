# MR11 Sine Test Report

Connected-load waveform behavior and timing accuracy notes for Riden PSU tests.

## Sine Wave Under Load

These captures show the MR11 lamp response under commanded waveform output. The key behavior is that measured output tracking is limited by poll/update timing, not wire baud alone.

![MR11 slow waveform response](waveform_slow.png)

Slow waveform reference capture.

![MR11 fast waveform response](waveform_fast.png)

Fast waveform reference capture.

## Connected-Load Timing Quick Matrix

### 100 ms cadence point

- RTT p50: 146.49 ms
- RTT p95: 147.15 ms
- p95-p50 jitter: 0.66 ms

### 150 ms cadence point

- RTT p50: 142.69 ms
- RTT p95: 146.53 ms
- p95-p50 jitter: 3.84 ms

Quick run artifacts:

- `connected_load_timing_matrix_quick.json`
- `connected_load_timing_matrix_quick.rtt.png`
- `connected_load_timing_matrix_quick.timeout.png`

## Timestamp and Accuracy Notes

- Timings are host-side wall-clock around request/response calls.
- Modbus RTU frames do not carry source timestamps from the PSU.
- No protocol field indicates exact ADC sample instant on device.
- Use distribution metrics (p50/p95/timeout rate), not single values.
- Wire theory (~2.69 ms for FC03 9-reg) is much lower than observed end-to-end timing.

This page should be read as a timing behavior report, not a deterministic per-sample device timestamp trace.

## Next Step (Exhaustive Matrix)

Run the larger connected-load matrix before final cadence recommendations:

```bash
python3 scripts/connected_load_timing_matrix.py \
  --port /dev/ttyUSB0 \
  --voltage 12 --current 1.5 \
  --poll-ms 20,50,100,150,200 \
  --samples 120 \
  --settle-s 3 \
  --out docs/connected_load_timing_matrix
```
