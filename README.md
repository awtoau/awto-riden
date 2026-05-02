# awto-mcp-riden

MCP server and CLI for RuiDeng/Riden RD60xx and RK6006 power supplies.

This repository provides a direct serial control stack for AI agents and humans:

- MCP tools (`rd_*`) for Copilot/Claude workflows
- CLI (`ttu_cli.py`) for bench scripting
- Timing profiler to recommend stable poll cadence
- Characterisation helpers (VI sweep, inrush capture, waveform output)

No external `riden` pip package is required. Transport and register logic are vendored here.

## Capabilities at a glance

Fastest mode (no cadence sleep) and paced cadences under connected load are now
captured in one combined graph:

![Connected-load timing capabilities](docs/data/timing_capabilities_overview.png)

Latest measured set summary:
[docs/timing_test_set_summary.md](docs/timing_test_set_summary.md)

Regenerate all timing artifacts in one command:

```bash
./scripts/regenerate_timing_artifacts.sh /dev/ttyUSB0 12 1.5
```

## Quick start

```bash
git clone https://github.com/awto-au/awto-mcp-riden
cd awto-mcp-riden
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

python3 ttu_cli.py --port /dev/ttyUSB0 status
python3 ttu_cli.py --port /dev/ttyUSB0 profile-serial --count 20 --sleep-ms 100
```

## Timing graphs (updated)

The graphs below were regenerated from session measurements and summarized in
[docs/serial_timing_profile.json](docs/data/serial_timing_profile.json).

For the dedicated MR11 waveform/timing page, see:
[docs/mr11_sine_test.md](docs/mr11_sine_test.md)

### 1) Poll pacing vs measured RTT

![Serial timing cadence sweep](docs/data/serial_timing_cadence_sweep.png)

### 2) Mode comparison with jitter bars

![Serial timing mode comparison](docs/data/serial_timing_mode_comparison.png)

Interpretation:

- Wire-time theory at 115200 baud for FC03(9 regs) is about 2.69 ms.
- Measured behavior is much higher and mode-dependent.
- Tight polling and paced polling can land in different scheduler phases.
- For control/logging, stable cadence is usually more important than minimum RTT.

## Why every read takes ~143 ms (the firmware scan floor)

You may notice that measured RTT is ~143 ms regardless of which registers you read,
how many you request, or how long the wire transfer takes.  This is not a code bug
and is not caused by the USB adapter.

**Root cause: the RD6006 firmware measurement scan runs at ~7 Hz (~143 ms/cycle).**

The PSU's embedded firmware samples its ADCs and refreshes its Modbus holding
registers once per firmware cycle, not on demand.  When the host issues a Modbus
read, the device does not reply immediately — it waits until the current firmware
cycle completes and the registers are refreshed, then sends the response.  This
produces a near-constant ~143 ms floor on every transaction regardless of register
count or baud rate.

**Consequence for set → read sequences:**

After a `write_register` (e.g. changing V_SET), the write acknowledgement also takes
~143 ms because it is queued behind the same firmware cycle.  The output hardware
reacts to the new setpoint almost immediately after the write is accepted, but
`v_out` and `i_out` readings will not reflect the new settled state until the *next*
firmware cycle — meaning you need to wait at least **one additional 143 ms cycle**
(~300 ms total) after a setpoint change before reading back a stable measurement.
Waiting two cycles (~300 ms) is a safe rule of thumb for settled readings.

This is an intrinsic hardware limit of the RD6006 firmware and cannot be improved
by tuning baud rate, USB latency timer, or pymodbus settings.

## Timing accuracy and timestamp semantics

This section documents what the timing data means and what it does not mean.

### What is timestamped

- Current measurements are host-side timings around the request/response call.
- In code, this is effectively `perf_counter()` before `write()` and after `read()`.
- So the value includes host scheduling, serial driver behavior, USB/BT transport,
  device turnaround, and response parsing delay.

### What Modbus provides (and does not provide)

- Standard Modbus RTU frames do not include source timestamps.
- The PSU response contains register data only, not capture-time metadata.
- Therefore there is no protocol-native way to recover exact device sampling instant.

### Practical error sources

- Host OS scheduler jitter
- USB serial bridge buffering/latency timer behavior
- Python runtime scheduling and GC pauses
- Device-side internal update cadence (phase effects)
- Link-specific effects (USB vs RFCOMM Bluetooth)

### How to read uncertainty from these measurements

Use distribution statistics, not one sample:

- `p50` approximates typical observed round-trip time
- `p95 - p50` is a useful jitter margin for cadence planning
- `max` highlights outlier stalls and should not be used as the steady-state target

For poll interval selection, prefer:

$$
  ext{poll\_ms} \approx \text{p95} + \text{headroom}
$$

Then quantize to practical scheduler buckets (20 ms or 50 ms).

## Serial profiling framework (USB and BT)

The same profiler output schema is available through CLI and MCP, so USB and
Bluetooth RFCOMM links can be compared directly.

CLI:

```bash
python3 ttu_cli.py --port /dev/ttyUSB0 profile-serial --count 20 --sleep-ms 100
```

MCP:

- `rd_profile_serial(count=20, sleep_ms=100)`
- `rd_capabilities()` includes `serial_profile` and `mcp_properties.recommended_poll_ms`

Profiler output fields include:

- `recommended_poll_ms`
- `raw_recommended_poll_ms`
- `quantization_ms`
- `timing.p50_ms`, `timing.p95_ms`, `timing.max_ms`, `timing.jitter_p95_minus_p50_ms`

## Connected-load timing matrix

For characterization-grade testing with a real load, use either the full test-set
runner (quick + comprehensive + analysis) or the direct matrix runner.

Recommended one-command test set (includes fastest/no-cadence point `0 ms`):

```bash
source .venv/bin/activate
python3 scripts/timing_test_set.py \
  --port /dev/ttyUSB0 \
  --voltage 12 --current 1.5 \
  --mode both \
  --quick-samples 12 \
  --comprehensive-samples 80 \
  --quick-poll-ms 0,100,150 \
  --comprehensive-poll-ms 0,20,50,100,150
```

Direct matrix runner:

```bash
source .venv/bin/activate
python3 scripts/connected_load_timing_matrix.py \
  --port /dev/ttyUSB0 \
  --voltage 12 --current 1.5 \
  --poll-ms 0,20,50,100,150 \
  --samples 120 \
  --settle-s 3 \
  --out docs/data/connected_load_timing_matrix
```

Outputs:

- `docs/data/connected_load_timing_matrix_quick.json`
- `docs/data/connected_load_timing_matrix_quick.rtt.png`
- `docs/data/connected_load_timing_matrix_quick.timeout.png`
- `docs/data/connected_load_timing_matrix_comprehensive.json`
- `docs/data/connected_load_timing_matrix_comprehensive.rtt.png`
- `docs/data/connected_load_timing_matrix_comprehensive.timeout.png`
- `docs/timing_test_set_summary.md`
- `docs/data/timing_capabilities_overview.png`

Why this is preferred over tiny sample sets:

- connected loads add thermal and state-dependent variability
- p95 and timeout rate are unstable at very low N
- cadence decisions should be based on repeated distributions, not single traces

Recommended minimums:

- 80 to 120 samples per cadence point
- 6+ cadence points
- optional repeat runs at different times if you need stronger confidence bounds

## MCP interface summary

Main categories:

- status/identity: `rd_status`, `rd_firmware`, `rd_capabilities`, `rd_profile_serial`
- control: `rd_set_voltage`, `rd_set_current`, `rd_output`, `rd_set_ovp`, `rd_set_ocp`, `rd_power_cycle`
- logging/characterisation: `rd_log_current`, `rd_log_status`, `rd_log_stop`, `rd_vsweep`, `rd_inrush_capture`, `rd_plot_results`
- raw access: `rd_modbus_read_holding`, `rd_modbus_write_register`
- multi-PSU: `rd_list_psus`, `rd_connect`, `rd_disconnect`, `rd_all_off`

## CLI interface summary

```bash
python3 ttu_cli.py --port /dev/ttyUSB0 [--baud 115200] [--address 1] <command>
```

Common commands:

- `status`
- `capabilities`
- `profile-serial`
- `set-voltage`
- `set-current`
- `output`
- `set-ovp`
- `set-ocp`
- `speed-test`
- `info`

## Architecture

```text
Agent/CLI
  -> mcp_server.py / ttu_cli.py
  -> riden_daemon.py (RidenWorker)
  -> riden_transport.py (pymodbus + raw serial fallback)
  -> Modbus RTU over USB or RFCOMM
  -> Riden PSU
```

## Safety and ops notes

- Check status before changing setpoints.
- Disable output before large voltage jumps.
- Treat CC at turn-on as expected inrush behavior unless persistent.
- Use `rd_all_off` for emergency multi-PSU shutdown.
