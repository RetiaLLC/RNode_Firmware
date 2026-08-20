#!/usr/bin/env python3
"""Build Retia RNode factory images for every supported board variant.

For each board in retia/boards.json this:
  1. compiles the variant with arduino-cli (esp32 core 2.0.17),
  2. merges a single 0x0 factory image (bootloader + partitions + app + SPIFFS console),
     producing BOTH a pre-provisioned image (canonical NVS baked at 0x9000 -> boots as a
     ready RNode, no rnodeconf) and an unprovisioned image, and
  3. asserts the image is safe to flash verbatim (DIO mode; flash-size header matches the
     module) and that the pre-provisioned NVS is present + checksum-valid.

Runs the same on a laptop and in CI. Output: dist/<asset>.factory.bin,
dist/<asset>-unprovisioned.factory.bin, dist/build-manifest.json.

Usage:
  python3 retia/build.py [--version X.YZ] [--boards id1,id2] [--outdir dist]
Env:
  ARDUINO_CLI  arduino-cli binary (default: arduino-cli)
  ESPTOOL      esptool binary     (default: esptool)
"""
import argparse, glob, hashlib, json, os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (has RNode_Firmware.ino)
RETIA = os.path.join(ROOT, "retia")
CORE_VER = "2.0.17"
BASE_FQBN_DOTTED = "esp32.esp32.esp32s3"   # arduino-cli -e export dir keys on the base fqbn
ARDUINO_CLI = os.environ.get("ARDUINO_CLI", "arduino-cli")
ESPTOOL = os.environ.get("ESPTOOL", "esptool")
FLASH_NIBBLE = {"1MB": 0, "2MB": 1, "4MB": 2, "8MB": 3, "16MB": 4}   # image-header byte[3] high nibble

def run(cmd, **kw):
    print("  $", " ".join(cmd)); sys.stdout.flush()
    subprocess.run(cmd, check=True, **kw)

def find_core_dir():
    for base in (os.path.expanduser("~/.arduino15"), os.path.expanduser("~/Library/Arduino15")):
        p = os.path.join(base, "packages/esp32/hardware/esp32", CORE_VER)
        if os.path.isdir(p):
            return p
    sys.exit(f"esp32 core {CORE_VER} not found under ~/.arduino15 or ~/Library/Arduino15")

def patch_bt_buffers(core_dir):
    """Makefile's check_bt_buffers only *verifies* RX>=6144/TX>=384; the core ships 512/32.
    Patch the source so the build's bt-buffer check passes (idempotent)."""
    f = os.path.join(core_dir, "libraries/BluetoothSerial/src/BluetoothSerial.cpp")
    if not os.path.exists(f):
        print("  (no BluetoothSerial.cpp; skipping bt-buffer patch)"); return
    s = open(f).read()
    s2 = re.sub(r"#define RX_QUEUE_SIZE \d+", "#define RX_QUEUE_SIZE 6144", s)
    s2 = re.sub(r"#define TX_QUEUE_SIZE \d+", "#define TX_QUEUE_SIZE 384", s2)
    if s2 != s:
        open(f, "w").write(s2); print("  patched BluetoothSerial buffers (RX=6144 TX=384)")

def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def assert_valid_nvs(nvs):
    for i in range(0, len(nvs) - 0x9C):
        if nvs[i:i+3] == bytes([0xF0, 0xFE, 0x01]) and nvs[i+0x9B] == 0x73 \
           and nvs[i+0x0B:i+0x1B] == hashlib.md5(nvs[i:i+0x0B]).digest():
            return True
    return False

def assert_flashable(path, flash_size):
    h = open(path, "rb").read(4)
    if h[0] != 0xE9:
        sys.exit(f"{path}: not an ESP image (magic {h[0]:#x})")
    if h[2] != 0x02:
        sys.exit(f"{path}: flash mode is {h[2]} not DIO(2) — QIO can brick some boards")
    want = FLASH_NIBBLE[flash_size]
    if (h[3] >> 4) != want:
        sys.exit(f"{path}: flash-size header nibble {h[3] >> 4} != {want} for {flash_size} "
                 f"(would boot-loop when flashed verbatim by esptool-js/scriptkitty)")

def build_board(b, core_dir, nvs, outdir, version):
    print(f"\n=== {b['id']} ({b['asset']}) ===")
    fqbn = f"{b['fqbn_opts'] and ('esp32:esp32:esp32s3:' + b['fqbn_opts']) or 'esp32:esp32:esp32s3'}"
    flags = " ".join(f'"{x}"' for x in b["flags"].split())
    run([ARDUINO_CLI, "compile", "--fqbn", fqbn, "-e",
         "--build-property", "build.partitions=no_ota",
         "--build-property", "upload.maximum_size=2097152",
         "--build-property", f"compiler.cpp.extra_flags={flags}"],
        cwd=ROOT)
    bd = os.path.join(ROOT, "build", BASE_FQBN_DOTTED)
    boot = os.path.join(bd, "RNode_Firmware.ino.bootloader.bin")
    part = os.path.join(bd, "RNode_Firmware.ino.partitions.bin")
    app  = os.path.join(bd, "RNode_Firmware.ino.bin")
    boot_app0 = os.path.join(core_dir, "tools/partitions/boot_app0.bin")
    console   = os.path.join(ROOT, "Release/console_image.bin")
    fs = b["flash_size"]
    common = ["--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", fs]
    results = []
    for suffix, with_nvs in (("", True), ("-unprovisioned", False)):
        out = os.path.join(outdir, f"{b['asset']}{suffix}.factory.bin")
        parts = ["0x0", boot, "0x8000", part]
        if with_nvs:
            parts += ["0x9000", os.path.join(RETIA, "factory-provisioned.nvs.bin")]
        parts += ["0xe000", boot_app0, "0x10000", app, "0x210000", console]
        run([ESPTOOL, "--chip", "esp32s3", "merge-bin", "-o", out] + common + parts)
        assert_flashable(out, fs)
        if with_nvs:
            data = open(out, "rb").read()
            if not assert_valid_nvs(data[0x9000:0xe000]):
                sys.exit(f"{out}: pre-provisioned NVS at 0x9000 is not valid")
        results.append({"asset": os.path.basename(out), "provisioned": with_nvs,
                        "sha256": sha256(out), "bytes": os.path.getsize(out)})
        print(f"  -> {os.path.basename(out)}  sha256 {results[-1]['sha256']}")
    return {"id": b["id"], "radio": b.get("radio"), "flash_size": fs, "images": results}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="0.0.0")
    ap.add_argument("--boards", default="")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()
    matrix = json.load(open(os.path.join(RETIA, "boards.json")))["boards"]
    if args.boards:
        want = set(args.boards.split(","))
        matrix = [b for b in matrix if b["id"] in want]
        if not matrix: sys.exit(f"no boards matched {args.boards}")
    core_dir = find_core_dir()
    patch_bt_buffers(core_dir)
    nvs = open(os.path.join(RETIA, "factory-provisioned.nvs.bin"), "rb").read()
    if not assert_valid_nvs(nvs):
        sys.exit("retia/factory-provisioned.nvs.bin does not contain a valid provisioned blob")
    os.makedirs(args.outdir, exist_ok=True)
    builds = [build_board(b, core_dir, nvs, args.outdir, args.version) for b in matrix]
    manifest = {"version": args.version, "core": CORE_VER, "builds": builds}
    json.dump(manifest, open(os.path.join(args.outdir, "build-manifest.json"), "w"), indent=2)
    print(f"\nBuilt {len(builds)} board(s) -> {args.outdir}")

if __name__ == "__main__":
    main()
