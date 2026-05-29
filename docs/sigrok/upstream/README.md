# Upstream submission — sigrok

sigrok takes patches via the **development mailing list**, not GitHub PRs
(see libsigrok's in-tree `HACKING`).

## Ready to send

`0001-rdtech-dps-fix-V-I-scaling-for-single-range-models-i.patch` — the
single-range multiplier fix (the airtight, hardware-proven one). Generated with
`git format-patch` against libsigrok master (`0bc24877`); message cites the
introducing regression (`02a4f485`) and the 3-device test evidence.

### Send it

1. Subscribe: <https://lists.sourceforge.net/lists/listinfo/sigrok-devel>
2. From a libsigrok checkout at master:
   ```bash
   git am docs/.../0001-rdtech-dps-...patch     # re-apply on a branch, or
   git send-email --to=sigrok-devel@lists.sourceforge.net 0001-rdtech-dps-...patch
   ```
   (set `sendemail.*` config first, or attach the file to a plain mail to the list).
3. Alternatively: push a branch to a public fork and notify the list / `#sigrok`
   IRC with the pull URL.

## NOT bundled (separate, lower priority)

- **RK6006 model add** (`../patches/libsigrok-rdtech-add-rk6006.patch`) — only
  serial V/I/P readout is verified; RK BLE/preset differences are not. Submit
  separately, if at all.
- **Python 3.14 configure** (`../patches/libsigrokdecode-python-3.14-configure.patch`)
  — worth submitting on its own; ideally raise switching to a version-agnostic
  `python3-embed` probe rather than a hardcoded list.
