"""Authored fixtures only: no Nintendo code or assets."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


generator = module("mk8gen", ROOT / "toolchain/recompiler/generate.py")
adapter = module("mk8prepare", ROOT / "runtime/prepare.py")
pipeline = module("mk8build", ROOT / "toolchain/build.py")


class SystemDataTests(unittest.TestCase):
    def test_optional_fonts_go_to_the_active_save_directory_and_legacy_mii_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'fonts'
            source.mkdir()
            with patch.object(pipeline, 'mirror') as junction:
                pipeline.prepare_system_data({'mii': str(root/'old-mii')}, root/'local')
                junction.assert_not_called()
                pipeline.prepare_system_data({'fonts': str(source)}, root/'local')
                junction.assert_called_once_with(root/'local/user-no-mii/fonts', str(source), copy_ok=True)



class MirrorTests(unittest.TestCase):
    def test_romfs_is_real_folders_of_hard_links_and_follows_a_new_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            romfs = root/'imports/romfs'
            (romfs/'Course/Gu_Menu').mkdir(parents=True)
            (romfs/'Course/Gu_Menu/course.szs').write_bytes(b'v1')
            (romfs/'Old.bin').write_bytes(b'old')
            path = root/'game-data/id/extracted/romfs'
            path.parent.mkdir(parents=True)
            # Older builds left a junction, which the launcher's RedirectionTrust refuses.
            subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(path), str(romfs)], check=True, capture_output=True)
            pipeline.mirror(path, romfs)
            self.assertFalse(path.is_junction())
            self.assertTrue((romfs/'Old.bin').is_file(), 'removing the junction must keep its target')
            self.assertTrue(os.path.samefile(path/'Course/Gu_Menu/course.szs', romfs/'Course/Gu_Menu/course.szs'))
            # A new import replaces files: links follow it and stale entries go away.
            (romfs/'Course/Gu_Menu/course.szs').unlink()
            (romfs/'Course/Gu_Menu/course.szs').write_bytes(b'v2')
            (romfs/'Old.bin').unlink()
            pipeline.mirror(path, romfs)
            self.assertEqual((path/'Course/Gu_Menu/course.szs').read_bytes(), b'v2')
            self.assertFalse((path/'Old.bin').exists())

def synthetic_nso(path):
    # A hand-authored function returns 42. NSO metadata describes a single export.
    text = bytearray(0x30)
    struct.pack_into("<II", text, 0, 0x14000008, 0x100)
    struct.pack_into("<II", text, 0x20, 0xd2800540, 0xd65f03c0)
    ro = bytearray(0x200)
    ro[:4] = b"MOD0"
    struct.pack_into("<6i", ro, 4, 0x20, 0x300, 0x300, 0x1f0, 0x1f0, 0)
    for offset, tag, value in [(0x20,4,0x180),(0x30,5,0x1a0),(0x40,6,0x1c0),(0x50,0,0)]:
        struct.pack_into("<QQ",ro,offset,tag,value)
    struct.pack_into("<II",ro,0x80,1,2)
    ro[0xa0:0xa9]=b"\x00fixture\x00"
    struct.pack_into("<IBBHQQ",ro,0xc0+24,1,0x12,0,1,0x20,8)
    ro[0x1f0:0x1f4] = bytes([1, 0x1b, 0x03, 0x3b])
    data = bytes(16)
    header = bytearray(256); header[:4]=b"NSO0"
    struct.pack_into("<I",header,12,0x38)
    file_off=256
    for i,(mem_off,segment) in enumerate([(0,text),(0x100,ro),(0x400,data)]):
        struct.pack_into("<III",header,0x10+i*16,file_off,mem_off,len(segment))
        struct.pack_into("<I",header,0x60+i*4,len(segment))
        header[0xa0+32*i:0xc0+32*i]=hashlib.sha256(segment).digest()
        file_off+=len(segment)
    path.write_bytes(header+text+ro+data)


class PipelineTests(unittest.TestCase):
    def test_game_identity_rejects_other_titles_and_bad_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.npdm"
            raw = bytearray(0xc0)
            raw[:4] = b"META"
            raw[0x0c] = 1
            struct.pack_into("<II", raw, 0x70, 0x80, 0x40)
            raw[0x80:0x84] = b"ACI0"
            struct.pack_into("<Q", raw, 0x90, 0x0100152000022000)
            path.write_bytes(raw)
            self.assertEqual(pipeline.validate_game_identity(path), "0100152000022000")
            raw[0x0c] = 4
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "AArch32"):
                pipeline.validate_game_identity(path)
            raw[0x0c] = 1
            path.write_bytes(raw)
            (path.parent / "subsdk9").write_bytes(b"fixture")
            self.assertTrue(any("subsdk9" in e for e in pipeline.preflight({"exefs": directory})))
            struct.pack_into("<Q", raw, 0x90, 0x0100000000010000)
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "Jogo incorreto"):
                pipeline.validate_game_identity(path)
            struct.pack_into("<I", raw, 0x70, 0xffffff00)
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "ACI0"):
                pipeline.validate_game_identity(path)

    def test_missing_inputs_fail_with_actionable_errors(self):
        errors=pipeline.preflight({})
        self.assertTrue(any("main.npdm" in e for e in errors))

    def test_nso_rejects_hash_corruption_and_large_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"fixture";synthetic_nso(p)
            self.assertEqual(len(generator.validate_nso(p,64*1024*1024)),64)
            raw=bytearray(p.read_bytes());raw[-1]^=1;p.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,"hash"):
                generator.validate_nso(p,64*1024*1024)
            synthetic_nso(p);raw=bytearray(p.read_bytes());struct.pack_into("<I",raw,0x18,0xffffffff);p.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,"bounds"):
                generator.validate_nso(p,64*1024*1024)

    def test_runtime_adapter_tracks_third_module_and_stops_unknown_hle(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/"source"
            result=adapter.prepare(ROOT/"upstream/smm2recomp",out)
            self.assertEqual(result["abi"],3)
            self.assertFalse(result["gameplay_verified"])
            dispatch=(out/"runtime/dispatch.c").read_text()
            self.assertIn("RECOMP_MODULE_COUNT",dispatch)
            self.assertNotIn("t < 2",dispatch)
            self.assertIn("required host service is not implemented",dispatch)
            self.assertIn("RECOMP_MODULES(TABLE)",(out/"tools/gamelib_entry.c").read_text())
            self.assertIn("X(subsdk4,", (out/"runtime/module_config.h").read_text())
            with self.assertRaisesRegex(ValueError,"fresh"):
                adapter.prepare(ROOT/"upstream/smm2recomp",out)

    def test_real_generator_and_resume_with_authored_nso(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);exefs=root/"exefs";exefs.mkdir();synthetic_nso(exefs/"main")
            profile={"modules":{"main":"0x7000000000"},"module_window_bytes":64*1024*1024,"simd":"c"}
            source=ROOT/"upstream/smm2recomp/recomp";output=root/"generated"
            generator.generate(source,exefs,output,profile)
            chunk=output/"main/main_000.c"
            self.assertTrue(chunk.is_file())
            text=chunk.read_text();self.assertIn("f_7000000020",text)
            stamp=chunk.stat().st_mtime_ns
            generator.generate(source,exefs,output,profile)
            self.assertEqual(stamp,chunk.stat().st_mtime_ns)
            chunk.write_text("corrupted")
            generator.generate(source,exefs,output,profile)
            self.assertEqual(text,chunk.read_text())
            receipt = output/'main-receipt.json'
            for damaged in ('{interrupted', '[]', '{"files": []}'):
                with self.subTest(receipt=damaged):
                    receipt.write_text(damaged)
                    chunk.write_text('incomplete generated C')
                    generator.generate(source,exefs,output,profile)
                    self.assertEqual(text, chunk.read_text())
                    self.assertEqual(json.loads(receipt.read_text())['files'][chunk.name], pipeline.sha(chunk))

    def test_instruction_patch_is_checked_and_translated(self):
        import lz4.block
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);exefs=root/"exefs";exefs.mkdir();synthetic_nso(exefs/"main")
            # The real modules are LZ4-compressed: compress the three segments of the fixture.
            raw=bytearray((exefs/"main").read_bytes());body=bytearray()
            for i in range(3):
                file_off,_,size=struct.unpack_from("<III",raw,0x10+i*16)
                packed=lz4.block.compress(bytes(raw[file_off:file_off+size]),store_size=False)
                struct.pack_into("<I",raw,0x10+i*16,256+len(body));struct.pack_into("<I",raw,0x60+i*4,len(packed));body+=packed
            struct.pack_into("<I",raw,12,0x3f);(exefs/"main").write_bytes(bytes(raw[:256])+body)
            original=pipeline.sha(exefs/"main")
            profile={"modules":{"main":"0x7000000000"},"module_window_bytes":64*1024*1024,"simd":"c"}
            source=ROOT/"upstream/smm2recomp/recomp";output=root/"generated"
            generator.generate(source,exefs,output,profile)
            self.assertIn("(uint64_t)(0x2aull)",(output/"main/main_000.c").read_text())
            # mov x0, #42 -> mov x0, #121: the receipt's input changes, so the module is translated again.
            patches_file=root/"4.0.1-patches.json"
            patches_file.write_text(json.dumps([{"module":"main","offset":"0x20","original":"0xd2800540",
                                                 "patched":"0xd2800f20","reason":"test"}]))
            patches=generator.load_patches(patches_file,profile["modules"])
            modules=generator.generate(source,exefs,output,profile,patches)
            self.assertIn("(uint64_t)(0x79ull)",(output/"main/main_000.c").read_text())
            self.assertEqual(modules["main"]["sha256"],original)  # (the hot list's '# input' hash stays the module's)
            self.assertEqual(pipeline.sha(exefs/"main"),original)
            self.assertEqual(list(output.glob("*.nso")),[])  # (the patched copy is temporary)
            with self.assertRaisesRegex(ValueError,"expects 0xd2800541, the module has 0xd2800540"):
                generator.generate(source,exefs,root/"other",profile,{"main":[(0x20,0xd2800541,0xd2800f20)]})
            for bad in ({"module":"sdk","offset":"0x20","original":"0x0","patched":"0x0","reason":"x"},
                        {"module":"main","offset":"0x22","original":"0x0","patched":"0x0","reason":"x"},
                        {"module":"main","offset":"0x20","original":"0x0","patched":"0x0","reason":""}):
                patches_file.write_text(json.dumps([bad]))
                with self.assertRaisesRegex(ValueError,"Invalid instruction patch"):
                    generator.load_patches(patches_file,profile["modules"])

    def test_missing_runtime_receipt_never_rebaselines_changed_sources(self):
        with tempfile.TemporaryDirectory(prefix='mk8-runtime-repair-') as temporary:
            job = Path(temporary)/'build'
            job.mkdir()
            saved = Path(temporary)/'user-save.bin'
            saved.write_bytes(b'preserve')
            profile = ROOT/'games/mk8d/profiles/development.json'
            prepared = pipeline.prepare_runtime(job, ROOT/'upstream/smm2recomp', profile)
            source = prepared/'runtime/dispatch.c'
            original = source.read_bytes()
            receipt = job/'runtime-hashes.json'
            self.assertTrue(pipeline.runtime_matches(prepared, receipt))
            stamp = source.stat().st_mtime_ns
            pipeline.prepare_runtime(job, ROOT/'upstream/smm2recomp', profile)
            self.assertEqual(stamp, source.stat().st_mtime_ns)
            receipt.unlink()
            source.write_bytes(b'damaged runtime source')
            with patch('storage.shutil.disk_usage', return_value=SimpleNamespace(free=3*1024**3)):
                with self.assertRaisesRegex(ValueError, 'preservar espaço'):
                    pipeline.prepare_runtime(job, ROOT/'upstream/smm2recomp', profile)
            self.assertEqual(source.read_bytes(), b'damaged runtime source')
            pipeline.prepare_runtime(job, ROOT/'upstream/smm2recomp', profile)
            self.assertEqual(source.read_bytes(), original)
            for invalid in ('{interrupted', '[]', '{}'):
                receipt.write_text(invalid)
                self.assertFalse(pipeline.runtime_matches(prepared, receipt))
            pipeline.prepare_runtime(job, ROOT/'upstream/smm2recomp', profile)
            self.assertTrue(pipeline.runtime_matches(prepared, receipt))
            self.assertEqual(saved.read_bytes(), b'preserve')


class RuntimeDependencyManifestTests(unittest.TestCase):
    def test_runtime_refresh_records_new_dll_without_changing_game_or_saves(self):
        with tempfile.TemporaryDirectory(prefix='mk8-runtime-refresh-') as temporary:
            local = Path(temporary)
            source = local/'runtime'
            source.mkdir()
            (local/'runtime-hashes.json').write_text('{}')
            (local/'generation.json').write_text(json.dumps({'source': str(source), 'identity': 'fixture'}))
            output = local/'host'
            output.mkdir()
            exe, game = output/'runtime.exe', output/'game.dll'
            exe.write_bytes(b'fixture runtime')
            game.write_bytes(b'preserved game')
            save = local/'user-save.bin'
            save.write_bytes(b'preserved save')
            (local/'candidate.json').write_text(json.dumps({'executable': pipeline.file_record(exe),
                'files': {'gamelib': pipeline.file_record(game)}, 'data_root': str(local/'data'), 'gameplay_verified': False}))
            def dependencies(*args, **kwargs):
                (output/'vulkan-1.dll').write_bytes(b'verified loader fixture')
            with patch('host.build_host', return_value=exe), patch.object(pipeline.subprocess, 'run', side_effect=dependencies), \
                    patch.object(pipeline, 'boot_check') as boot:
                pipeline.refresh_runtime(local)
            boot.assert_called_once_with([exe, local/'data/extracted'], str(game), local)
            candidate = json.loads((local/'candidate.json').read_text())
            self.assertEqual(candidate['files']['vulkan-1.dll'], pipeline.file_record(output/'vulkan-1.dll'))
            self.assertEqual(candidate['files']['gamelib'], pipeline.file_record(game))
            self.assertEqual(save.read_bytes(), b'preserved save')
            self.assertFalse(candidate['gameplay_verified'])


class BootCheckTests(unittest.TestCase):
    """The build's title-screen check, with stand-in programs for the runtime."""
    GAMES = {
        'title': "import os, pathlib; pathlib.Path(os.environ['LARECOMP_SHOT_PATH']).write_bytes(b'png')",
        'crash': "print('FATAL: no function at 7000001234'); raise SystemExit(3)",
        'hang': "import time; time.sleep(60)",
    }

    def check(self, game, timeout=60):
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder)
            script = local/'game.py'
            script.write_text(self.GAMES[game], encoding='utf-8')
            try:
                with patch.dict(os.environ, {'MK8_SKIP_BOOT_CHECK': ''}):
                    pipeline.boot_check([sys.executable, script], local/'game.dll', local, timeout=timeout)
            finally:
                log = (local/'boot-check.log').read_text(encoding='utf-8', errors='replace')
        return log

    def test_title_screen_passes(self):
        self.check('title')

    def test_crash_or_hang_is_not_published(self):
        with self.assertRaisesRegex(ValueError, 'FATAL: no function at 7000001234.*candidato não publicado'):
            self.check('crash')
        with self.assertRaisesRegex(ValueError, 'candidato não publicado'):
            self.check('hang', timeout=2)


if __name__=="__main__":
    unittest.main()
