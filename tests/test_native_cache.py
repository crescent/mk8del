"""Shared object cache across build identities, exercised with the real pinned compiler and synthetic units."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'toolchain'))
import native
from native import DEFAULT_COMPILER

HEADER = 'typedef struct {{ unsigned long long x[32]; }} Cpu;\n#define BIAS {bias}\n'
UNIT = '#include "cpu.h"\nvoid f_{name}(Cpu *c) {{ c->x[0] = c->x[1] * {mul} + BIAS; }}\n'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_build(builds, name, bias=1):
    job = builds/name
    runtime = job/'runtime'
    (runtime/'runtime').mkdir(parents=True)
    (runtime/'tools').mkdir()
    (runtime/'runtime/cpu.h').write_text(HEADER.format(bias=bias), encoding='utf-8')
    (runtime/'tools/gamelib_entry.c').write_text('int mk8_entry_marker(void) { return 8; }\n', encoding='utf-8')
    generated = job/'generated/main'
    generated.mkdir(parents=True)
    (generated/'main_table.c').write_text('const int main_table[] = {1, 2, 3};\n', encoding='utf-8')
    for i in range(3):
        (generated/f'main_00{i}.c').write_text(UNIT.format(name=f'70000{i}', mul=i + 3), encoding='utf-8')
    return job


def compile_build(job, cache):
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        native.compile_modules(job/'runtime', job/'generated', job/'native-optimized', ['main'],
                               optimization='-O1', workers=2, cache=cache)
    stamps = {p.stem: json.loads(p.read_text()) for p in (job/'native-optimized/main').glob('*.json')}
    stamps['gamelib_entry'] = json.loads((job/'native-optimized/gamelib_entry.json').read_text())
    return stamps, log.getvalue()


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class NativeCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='mk8-objcache-')
        self.root = Path(self.folder.name)
        self.builds = self.root/'builds'
        self.cache = self.root/'cache'

    def tearDown(self):
        self.folder.cleanup()

    def test_new_identity_with_same_sources_reuses_every_object(self):
        first, _ = compile_build(make_build(self.builds, 'a'), self.cache)
        self.assertEqual(len(first), 5)
        self.assertTrue(all('seconds' in s for s in first.values()), 'first build must really compile')
        second_job = make_build(self.builds, 'b')
        second, log = compile_build(second_job, self.cache)
        self.assertTrue(all('seconds' not in s for s in second.values()), 'same sources must not recompile')
        self.assertIn('5 de 5 unidades reaproveitadas', log)
        for stem, stamp in second.items():
            self.assertEqual(stamp['cache'], first[stem]['cache'])
        # The reused object is the compiled one, and the job's archive is built from it.
        a = self.builds/'a/native-optimized/main/main_001.o'
        b = second_job/'native-optimized/main/main_001.o'
        self.assertEqual(sha(a), sha(b))
        self.assertTrue((second_job/'native-optimized/mk8d-game-code.a').is_file())

    def test_header_change_recompiles_instead_of_reusing(self):
        compile_build(make_build(self.builds, 'a', bias=1), self.cache)
        changed, log = compile_build(make_build(self.builds, 'b', bias=2), self.cache)
        self.assertTrue(all('seconds' in s for s in changed.values()))
        self.assertNotIn('reaproveitadas', log)

    def test_only_included_headers_count(self):
        compile_build(make_build(self.builds, 'a'), self.cache)
        unrelated = make_build(self.builds, 'b')
        (unrelated/'runtime/runtime/menu.h').write_text('int menu_changed(void);\n', encoding='utf-8')
        _, log = compile_build(unrelated, self.cache)
        self.assertIn('5 de 5 unidades reaproveitadas', log)
        # A header reached through another one still counts.
        nested = make_build(self.builds, 'c')
        (nested/'runtime/runtime/cpu.h').write_text(HEADER.format(bias=1) + '#include "bias.h"\n', encoding='utf-8')
        (nested/'runtime/runtime/bias.h').write_text('#define EXTRA 1\n', encoding='utf-8')
        third, _ = compile_build(nested, self.cache)
        (nested/'runtime/runtime/bias.h').write_text('#define EXTRA 2\n', encoding='utf-8')
        fourth, _ = compile_build(nested, self.cache)
        self.assertNotEqual(third['main_001']['input']['environment'], fourth['main_001']['input']['environment'])
        self.assertIn('seconds', fourth['main_001'])

    def test_corrupted_cache_entry_is_rebuilt_not_trusted(self):
        first, _ = compile_build(make_build(self.builds, 'a'), self.cache)
        address = first['main_002']['cache']
        entry = self.cache/address[:2]/(address + '.o')
        # Break the link to the job's object first, then damage only the cache copy.
        data = entry.read_bytes(); entry.unlink(); entry.write_bytes(data[:-8] + b'\0' * 8)
        damaged = sha(entry)
        second, _ = compile_build(make_build(self.builds, 'b'), self.cache)
        self.assertIn('seconds', second['main_002'])
        self.assertNotIn('seconds', second['main_001'])
        # (Recompiled objects differ only in CodeView temp paths, so compare against the damaged bytes.)
        self.assertNotEqual(sha(self.builds/'b/native-optimized/main/main_002.o'), damaged)
        self.assertIsNotNone(native.cache_lookup(self.cache, address), 'recompilation repairs the cache entry')

    def test_existing_builds_seed_an_empty_cache(self):
        compile_build(make_build(self.builds, 'a'), self.root/'old-cache')
        fresh = self.root/'fresh-cache'
        second, log = compile_build(make_build(self.builds, 'b'), fresh)
        self.assertIn('5 objetos de builds anteriores registrados', log)
        self.assertTrue(all('seconds' not in s for s in second.values()))
        # A tampered object of an earlier build is not registered.
        third_cache = self.root/'third-cache'
        victim = self.builds/'a/native-optimized/main/main_000.o'
        data = victim.read_bytes(); victim.unlink(); victim.write_bytes(data + b'x')
        (self.builds/'b').rename(self.root/'b-moved')
        self.assertEqual(native.seed_cache(self.builds, third_cache), 4)

    def test_invalid_receipts_do_not_block_resuming_a_build(self):
        job = make_build(self.builds, 'repair')
        first, _ = compile_build(job, self.cache)
        for name, content in [('main_000', '{broken'), ('main_001', '[]')]:
            (job/f'native-optimized/main/{name}.json').write_text(content)
            address = first[name]['cache']
            (self.cache/address[:2]/(address + '.json')).write_text(content)
        second, _ = compile_build(job, self.cache)
        self.assertIn('seconds', second['main_000'])
        self.assertIn('seconds', second['main_001'])
        self.assertEqual(first['main_002'], second['main_002'])
        for name in ('main_000', 'main_001'):
            self.assertIsNotNone(native.cache_lookup(self.cache, second[name]['cache']))

    def test_key_ignores_only_the_job_include_path(self):
        key = {'source': 'a' * 64, 'environment': 'b' * 64,
               'flags': ['-O1', '-IC:\\x\\builds\\one\\runtime\\runtime']}
        other = dict(key, flags=['-O1', '-IC:\\x\\builds\\two\\runtime\\runtime'])
        self.assertEqual(native.cache_key(key), native.cache_key(other))
        self.assertNotEqual(native.cache_key(key), native.cache_key(dict(key, flags=['-O2', key['flags'][1]])))
        self.assertNotEqual(native.cache_key(key), native.cache_key(dict(key, source='c' * 64)))


if __name__ == '__main__':
    unittest.main()
