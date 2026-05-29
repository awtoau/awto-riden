# sigrok / PulseView with the Riden RD60xx

How to drive a Riden RD6024 (and siblings) from **sigrok-cli / PulseView** via
the native `rdtech-rd` driver, why it needs building from source, and the two
patches required on this host.

## TL;DR

The Riden is natively supported by libsigrok's `rdtech-dps` source (registers
two drivers: `rdtech-dps` for DPS/DPH, `rdtech-rd` for Riden RD60xx). But:

- The last sigrok **release** is libsigrok **0.5.2 (Aug 2019)**; the Riden
  driver is **master-only** (~1850 commits past 0.5.2). Distro packages
  (Fedora `libsigrok-0.5.2`) therefore lack it entirely.
- Building master needs **two local patches** (below): one real upstream bug
  fix, one for Python 3.14.

After building the stack into `/usr/local`:

```bash
export LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib64
/usr/local/bin/sigrok-cli --driver rdtech-rd:conn=/dev/ttyUSB0 --scan
# → rdtech-rd - RDTech RD6024 v1.39 [S/N: 4826] with 3 channels: V I P

/usr/local/bin/pulseview --driver rdtech-rd:conn=/dev/ttyUSB0   # GUI, then Run
/usr/local/bin/sigrok-cli --driver rdtech-rd:conn=/dev/ttyUSB0 --continuous -O csv > psu.csv
```

## Source provenance

Canonical upstream is **`git://sigrok.org/<repo>`**. sigrok does **not** use
GitHub PRs — `github.com/sigrokproject/*` is an official *mirror*. Verified the
GitHub mirror == canonical at build time: libsigrok HEAD
`0bc2487778e660f4d3116729b6f4aee2b1996bb0` on both.

Mirrored locally via `awto-dan/scripts/get_to_mirror.py` (GitHub-only) to
`/mnt/2tb/git_mirror/sigrokproject/<repo>.git` (bare) + working tree at
`/mnt/2tb/git/sigrokproject/<repo>`.

## Build process

Whole stack from master into `/usr/local` (coexists with — or replaces — distro
packages; `/usr/local/bin` and `/usr/local/lib` take precedence). Script:
[`scripts/build_sigrok_stack.sh`](../../scripts/build_sigrok_stack.sh).

Dependency order: `libserialport → libsigrok → libsigrokdecode → sigrok-cli → pulseview`.

### Build deps (Fedora)

```bash
sudo dnf install -y \
  glib2-devel libzip-devel libusb1-devel libftdi-devel libserialport-devel \
  glibmm24-devel check-devel \
  qt5-qtbase-devel qt5-qtsvg-devel boost-devel python3-devel \
  autoconf automake libtool pkgconf-pkg-config gcc gcc-c++ cmake make swig
```

> **Note:** removing the distro `libsigrok*`/`pulseview` packages also removes
> their `-devel` deps (`libserialport-devel`, `glibmm2.4-devel`,
> `libsigc++20-devel`, `libzip-devel`). Re-install the deps above before a clean
> rebuild.

### fx2lafw firmware (FX2-based logic analyzers)

Separate repo, needs the **SDCC** 8051 compiler (Fedora binary is `sdcc-sdcc`):

```bash
sudo dnf install -y sdcc
cd /mnt/2tb/git/sigrokproject/sigrok-firmware-fx2lafw
./autogen.sh && ./configure --prefix=/usr/local && make -j"$(nproc)" && sudo make install
```

Installs `.fw` images to `/usr/local/share/sigrok-firmware/` (searched before
`/usr/share`). Built version here: `0.1.7-10-g0f2d324` (2024-02).

## The two patches

Both live in [`patches/`](patches/) and are applied to the working trees on
`/mnt/2tb` before building. They are NOT yet upstreamed (see below).

### 1. libsigrok — `rdtech-rd` single-range multiplier init (real bug)

[`patches/libsigrok-rdtech-single-range-multipliers.patch`](patches/libsigrok-rdtech-single-range-multipliers.patch)

**Symptom:** acquisition returns `V: inf, I: -nan` (P is fine — it uses a
hardcoded `/100`).

**Cause:** `rdtech_dps_update_range()` early-returned for `n_ranges <= 1`
*before* calling `rdtech_dps_update_multipliers()`. Single-range models
(RD6006/6012/6018/**6024**, and one-range DPS models) never report a RANGE
register, so the acquisition path's `STATE_RANGE` multiplier update also never
fires. Result: `voltage_multiplier`/`current_multiplier` stay at their zero-init
value, and every reading is `raw / 0` → `inf`, or `0 / 0` → `nan`. The same zero
denominator hits `config_list` (`1 / devc->voltage_multiplier`).

**Fix:** for `n_ranges <= 1`, set `curr_range = 0` and call
`update_multipliers()` (then return). `probe_device()` already calls
`update_range()` immediately after setting the model, so this is the correct,
intended init point — the patch just makes it do its job for single-range
devices. Verified: `V: 4.99` (was inf), `I: 0.00` (was nan) against a live
RD6024.

**Scope — this is bigger than it looks.** Of the whole Riden line, only the
**RD6012P** is multi-range. RD6006 / RD6006P / RD6012 / RD6018 / **RD6024** are
all single-range — so the un-patched driver reports inf/nan for **essentially
every Riden model**. All four `update_multipliers()` call sites in the upstream
driver are gated behind a multi-range or range-*change* condition
(`update_range` early-returns at `n_ranges <= 1`; the `get_state` calls fire
only on `curr_range != state.range`, which never trips when both are the
zero-init 0). Nothing initialises the multipliers for a single-range device.

Caveat: it's unclear how this shipped broken for nearly the entire Riden line
unnoticed — likely because `rdtech-rd` is master-only (never released) and
lightly used. But the breakage and the fix are both confirmed against real
hardware. Note the same zero divisor also hits the **config / "cal"-style**
paths: `config_list` does `1 / devc->voltage_multiplier` (api.c), so
voltage/current step queries are affected too, not just live acquisition.

**What a Riden "range" actually is.** Every Riden is single operating-range in
the normal sense (one V/I envelope per model). The only model the driver marks
multi-range is the **RD6012P**, and even that is not a second operating range —
it's a *current-resolution* switch: the `I_RANGE` register selects 4-digit
(≤6 A) vs 3-digit (≤12 A) current scaling. awto-riden models this directly
(`riden_daemon.py`: `i_multi = 10000 if I_RANGE == 0 else 1000`); sigrok models
it as two "ranges" (`{6A, …, 4, 3}` / `{12A, …, 3, 3}`) because "range" is its
mechanism for a runtime multiplier change. So:

- RD6006 / RD6006P / RD6012 / RD6018 / RD6024 → genuinely single-range → all
  hit by the multiplier-init bug; fixed by this patch.
- RD6012P → the one "multi-range" entry, only to express the resolution switch;
  its range-change path already initialises multipliers, so it was unaffected
  and the patch leaves it alone.

**Definitely worth submitting upstream.**

### 2. libsigrokdecode — Python 3.14 in configure (host-specific)

[`patches/libsigrokdecode-python-3.14-configure.patch`](patches/libsigrokdecode-python-3.14-configure.patch)

`configure.ac`'s `SR_PKG_CHECK([python3], ...)` version list stopped at
`python-3.12-embed`. This host runs Python 3.14, so configure fell back to a
python without proper include flags → `fatal error: Python.h: No such file or
directory`. Patch prepends `python-3.14-embed`, `python-3.13-embed`.

(Less urgent to upstream — it's a moving target as Python advances — but the
fix pattern is correct.)

## Upstreaming

sigrok uses the **development mailing list**, not GitHub PRs:

- `sigrok-devel@lists.sourceforge.net` (subscribe first) — `git format-patch` +
  `git send-email`, or
- host a branch anywhere and notify via the list / `#sigrok` IRC.

See the in-tree `HACKING` file. The libsigrok multiplier fix (patch 1) is the
one worth submitting.

## Python / venv note (the awto-riden side)

Unrelated to sigrok but recorded here while in the area: awto-riden's
`pyproject.toml` declares `[tool.uv] python = "3.14t"` (free-threaded) but the
active `.venv` is the **standard GIL-enabled** 3.14 (`sys._is_gil_enabled()` is
True). The coding-style guide mandates the free-threaded build (`.venv-ft/`, via
`uv venv --python python3.14t`). Tracked in the agent-assist review queue, not
yet done. The discovery parallelism works under the GIL (blocking serial reads
release it) but the stated model is free-threaded.
