"""Strict NSO validation and resumable AArch64-to-C generation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
from pathlib import Path
import struct
import subprocess
import sys
import time


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def validate_nso(path, window):
    import lz4.block
    raw = Path(path).read_bytes()
    if len(raw) < 256 or raw[:4] != b"NSO0":
        raise ValueError(f"Invalid NSO: {path}")
    flags = struct.unpack_from("<I", raw, 12)[0]
    ranges = []
    for i in range(3):
        file_off, mem_off, size = struct.unpack_from("<III", raw, 0x10 + i*16)
        stored = struct.unpack_from("<I", raw, 0x60+i*4)[0] if flags & (1 << i) else size
        if size > window or mem_off + size > window or file_off < 256 or file_off + stored > len(raw):
            raise ValueError(f"NSO segment outside supported bounds: {path}")
        data = raw[file_off:file_off+stored]
        if flags & (1 << i):
            data = lz4.block.decompress(data, uncompressed_size=size)
        if len(data) != size:
            raise ValueError("NSO decompressed size mismatch")
        if flags & (1 << (i+3)) and hashlib.sha256(data).digest() != raw[0xa0+32*i:0xc0+32*i]:
            raise ValueError("NSO segment hash mismatch")
        if size:
            if any(mem_off < end and start < mem_off+size for start,end in ranges):
                raise ValueError("NSO overlapping segments")
            ranges.append((mem_off,mem_off+size))
    bss = struct.unpack_from("<I",raw,0x3c)[0]
    data_off, data_size = struct.unpack_from("<II",raw,0x34)
    if data_off+data_size+bss>window:
        raise ValueError("NSO BSS exceeds reserved module window")
    return raw[0x40:0x60].hex()


def load_patches(path, modules):
    """Instruction patches by module: [{"module", "offset", "original", "patched", "reason"}], hex strings."""
    patches = {}
    for entry in json.loads(Path(path).read_text(encoding="utf-8")):
        if set(entry) != {"module", "offset", "original", "patched", "reason"} or entry["module"] not in modules or not entry["reason"]:
            raise ValueError(f"Invalid instruction patch: {entry}")
        offset, original, patched = (int(entry[key], 16) for key in ("offset", "original", "patched"))
        if offset % 4 or not (0 <= original < 2**32 and 0 <= patched < 2**32):
            raise ValueError(f"Invalid instruction patch: {entry}")
        patches.setdefault(entry["module"], []).append((offset, original, patched))
    return patches


def patched_nso(path, patches, target):
    """An uncompressed copy of the NSO with each word replaced; the original word must be the one recorded.

    Only the translation reads the copy: the runtime still maps the original module, so code the game reads as data
    keeps the old word.
    """
    import lz4.block
    raw = Path(path).read_bytes()
    flags = struct.unpack_from("<I", raw, 12)[0]
    segments = []
    for i in range(3):
        file_off, mem_off, size = struct.unpack_from("<III", raw, 0x10 + i*16)
        stored = struct.unpack_from("<I", raw, 0x60+i*4)[0] if flags & (1 << i) else size
        data = raw[file_off:file_off+stored]
        segments.append([mem_off, bytearray(lz4.block.decompress(data, uncompressed_size=size) if flags & (1 << i) else data)])
    for offset, original, patched in patches:
        segment = next((s for s in segments if s[0] <= offset and offset + 4 <= s[0] + len(s[1])), None)
        if segment is None:
            raise ValueError(f"{path.name}: patch at {offset:#x} is outside the module")
        found = struct.unpack_from("<I", segment[1], offset - segment[0])[0]
        if found != original:
            raise ValueError(f"{path.name}: patch at {offset:#x} expects {original:#010x}, the module has {found:#010x}")
        struct.pack_into("<I", segment[1], offset - segment[0], patched)
    header = bytearray(raw[:0x100])
    struct.pack_into("<I", header, 12, flags & ~0b111)  # (no compression; hashes rewritten below)
    out = bytearray(raw[0x100:min(struct.unpack_from("<I", raw, 0x10 + i*16)[0] for i in range(3))])
    for i, (mem_off, data) in enumerate(segments):
        struct.pack_into("<III", header, 0x10 + i*16, 0x100 + len(out), mem_off, len(data))
        struct.pack_into("<I", header, 0x60 + i*4, len(data))
        header[0xa0+32*i:0xc0+32*i] = hashlib.sha256(data).digest()
        out += data
    target.write_bytes(header + out)
    return target


def generate(source, exefs, output, profile, patches=None):
    output.mkdir(parents=True, exist_ok=True)
    patches = patches or {}
    tools = source / "tools"
    generator_id = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(tools.glob("*.py")))).hexdigest()

    def generate_module(module):
        name, base = module
        path = exefs / name
        build_id = validate_nso(path, profile["module_window_bytes"])
        identity = {"sha256": sha(path), "build_id": build_id, "base": base, "generator": generator_id, "simd": profile["simd"]}
        if name in patches:
            identity["patches"] = [[f"{o:#x}", f"{a:#010x}", f"{b:#010x}"] for o, a, b in patches[name]]
        target = output / name
        receipt = output / (name + "-receipt.json")
        if receipt.is_file():
            try:
                previous = json.loads(receipt.read_text(encoding='utf-8'))
                files = previous.get("files") if isinstance(previous, dict) else None
                if isinstance(files, dict) and files and previous.get("input") == identity and set(files) == {p.name for p in target.glob("*.c")} and all((target / p).is_file() and sha(target / p) == h for p,h in files.items()):
                    print(f"{name}: cache verificado", flush=True)
                    return name, identity
            except (FileNotFoundError, ValueError):
                pass  # Interrupted metadata cannot prove that generated code is reusable.
        stage = output / f"{name}-pending-{time.time_ns()}"
        translated = path
        if name in patches:
            translated = patched_nso(path, patches[name], output / f"{name}-patched-{time.time_ns()}.nso")
            print(f"{name}: {len(patches[name])} instrução(ões) substituída(s) antes da tradução", flush=True)
        try:
            subprocess.run([sys.executable, tools / "gen.py", name, translated, base, stage, "--simd", profile["simd"]], cwd=tools, check=True)
        finally:
            if translated != path:
                translated.unlink()
        if sha(path) != identity["sha256"]:
            raise ValueError(f"{name}: input changed while generating")
        if not list(stage.glob("*.c")):
            raise ValueError(f"{name}: no generated C")
        table = (stage / (name + "_table.c")).read_text()
        match = re.search(r"_fn_count\s*=\s*(\d+)", table)
        if not match or int(match.group(1)) == 0:
            raise ValueError(f"{name}: no discovered functions; unsupported module layout")
        if target.exists():
            target.rename(output / f"{name}-previous-{time.time_ns()}")
        stage.rename(target)
        files = {p.name:sha(p) for p in target.glob("*.c")}
        temp = receipt.with_suffix(".pending")
        temp.write_text(json.dumps({"input": identity,"files":files},indent=2),encoding="utf-8")
        os.replace(temp,receipt)
        return name, identity

    # The modules share read-only tools but each publishes to its own directory.
    modules = profile["modules"]
    with ThreadPoolExecutor(max_workers=max(1, min(3, len(modules), os.cpu_count() or 1))) as pool:
        identities = dict(pool.map(generate_module, modules.items()))
    (output / "MODULES.json").write_text(json.dumps(identities,indent=2),encoding="utf-8")
    return identities


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    for name in ("source","exefs","output","profile"):
        parser.add_argument("--"+name,required=True,type=Path)
    parser.add_argument("--patches",type=Path,help="checked instruction patches (load_patches)")
    args=parser.parse_args()
    profile=json.loads(args.profile.read_text())
    patches=load_patches(args.patches,profile["modules"]) if args.patches else None
    generate(args.source.resolve(),args.exefs.resolve(),args.output.resolve(),profile,patches)
