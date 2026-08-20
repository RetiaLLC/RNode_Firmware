# Retia RNode — fork + release system

This is a curated fork of [markqvist/RNode_Firmware](https://github.com/markqvist/RNode_Firmware).
Retia board support (Nibble line, Newsheen/Pusheen puck, DEF CON badge) is carried **in-tree**
on the `retia-stable` branch — see `Boards.h` (`BOARD_RETIA_*`), `sx126x.cpp`, `Utilities.h`,
`Display.h`. It is a file-import fork (no shared git ancestry with upstream).

**Upstream lineage:** firmware **1.85** (`Config.h` `MAJ_VERS`/`MIN_VERS`). Upstream has since
moved to 1.86. Because the board patches live in-tree, upstream is **merged deliberately by a
human**, never automatically — a bad auto-merge would break every board at once. The release
workflow surfaces upstream drift as a notice so you know when to merge.

## Release system (matches the scriptkitty.sh channel contract)

Everything compiles for **all supported board variants** and ships **gated** to scriptkitty.sh:
auto-builds are untested prereleases; a human promotes one to verified after flashing hardware.

| Piece | What it does |
|---|---|
| `retia/boards.json` | The board matrix — one entry per variant (fqbn opts, `-D` flags, flash size, stable asset name). Add a board here. |
| `retia/build.py` | Compiles every board with arduino-cli (esp32 core **2.0.17**) and merges a 0x0 factory image — both **pre-provisioned** (canonical NVS baked at 0x9000 → boots as a ready RNode, no `rnodeconf`) and unprovisioned. Asserts DIO mode + a flash-size header that matches the module (the 16 MB boot-loop trap) + a valid provisioned NVS. Runs the same locally and in CI. |
| `retia/factory-provisioned.nvs.bin` | Canonical pre-provision NVS (namespace `eeprom` blob): Homebrew RNode, product `f0` / model `fe` / hwrev 1, checksum-valid, info-locked. Board- and radio-agnostic. Extracted from a hardware-verified release image. |
| `.github/workflows/rnode-release.yml` | Weekly (+ on demand) — builds **all** variants, publishes one **prerelease** `rnode-v<ver>-sk.<n>` with every `*.factory.bin`. If any variant stops compiling, the run fails and nothing ships. |
| `.github/workflows/rnode-promote.yml` | Attaches `verification-<board>.json` for each board actually flashed + verified, flips the release to full, pings the site. Verification is **per board**. |

### Build locally
```bash
python3 retia/build.py --version 1.85               # all boards -> dist/
python3 retia/build.py --version 1.85 --boards newsheen   # one board
```

### Cut / promote
```bash
gh workflow run rnode-release.yml --repo RetiaLLC/RNode_Firmware -f notes="why"
gh workflow run rnode-promote.yml --repo RetiaLLC/RNode_Firmware \
  -f tag=rnode-v1.85-sk.1 -f device=newsheen -f method=human -f notes="boots ready; radio online + TX"
```

Each board is one scriptkitty channel card: `binary_source: channel`, `channel.repo:
RetiaLLC/RNode_Firmware`, `asset_pattern: rnode-<board>.factory.bin`, `verification_pattern:
verification-<board>.json`. A card serves its newest **verified** build as the default and lists
untested auto-builds behind a warning.
