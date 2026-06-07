# Riden / RuiDeng firmware — known versions per model

Single source of truth for **latest-known firmware per model**, consumed by the
`rd_firmware` MCP tool (replaces the hardcoded table in `mcp_server.py`).

The PSU reports firmware as a raw integer in Modbus register **3 (FW)** = version × 100
(e.g. raw `142` = v1.42).

## Two firmware tracks
- **Stock** — RuiDeng official. Plain version (e.g. `RD60062_V1.42.bin`).
- **UniSoft** — community custom firmware (adds features), version + `.1x` suffix
  (e.g. `RD60062_V1.42.1p.bin`). The device FW register reports the base version.

## Latest known (PSU models)

| Model | ID (reg 0) | Stock latest | raw | UniSoft latest | Confirmed via |
|---|---|---|---|---|---|
| RD6006  | 60062 | **v1.42** | 142 | v1.42.1p | RDFlasher bundle (stock 2024‑02; UniSoft 2025‑09) |
| RD6006P | 60065 | **v1.45** | 145 | v1.45.1p | RDFlasher bundle |
| RD6012  | 60121 | **v1.37** | 137 | v1.37.1p | RDFlasher bundle |
| RD6018  | 60181 | **v1.39** | 139 | v1.39.1p | RDFlasher bundle |
| RK6006  | 60066 | v1.09 | 109 | — | on device (SN 00001036); latest unconfirmed |
| RD6012P | 60125 | v1.14¹ | 114 | — | tjko/riden-flashtool (not re‑confirmed) |
| RD6024  | 60241 | v1.13¹ | 113 | — | tjko/riden-flashtool (not re‑confirmed) |
| RD6018W | 60181 | v1.12¹ | 112 | — | tjko/riden-flashtool (WiFi variant; same id as RD6018) |
| RD6030  | 60301 | — | — | — | no image obtained yet |

¹ Carried over from the old table; **not** re-confirmed — vendor's live update URL is dead
(see Sources), so these may be behind.

## USB testers (separate product line)
| Model | Latest |
|---|---|
| TC66 / TC66C | v1.18 |

## Sources
- **RDFlasher bundle** (`RDFlasher_v1p.zip`) — stock + UniSoft images for RD6006/6006P/6012/6018,
  multiple versions. Most current archive found (2025‑09).
- **wildekek/rdtech-firmware-unisoft** — UniSoft custom firmware mirror.
- **tjko/riden-flashtool**, **rssdev10/riden-flashtool**, **ShayBox/Riden** — tooling + some images.
- **Vendor:** `http://www.ruidengkeji.com/?resource/` (download page). The old direct path
  `…/rdupdate/firmware/<MODEL>/<MODEL>.{json,bin}` now **301‑redirects to the homepage (dead)**,
  so automated "latest" checks against the vendor no longer work — update this table from the
  bundles/mirrors above or the resource page.

## Maintenance
Update the **Stock latest / raw** column when a newer image is confirmed; `rd_firmware`
compares the connected PSU's reg‑3 raw value against the `raw` here.
