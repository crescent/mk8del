"""A copied NVNsampler keeps its state after the original is finalized and its memory reused."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import DEPS, prepare_host_sources
from native import DEFAULT_COMPILER
import sampler_inline


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class SamplerInlineTests(unittest.TestCase):
    def test_copies_survive_finalize(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            objects = (runtime/'nvn/objects.c').read_text(encoding='utf-8')
            self.assertNotIn('HOST(NvnSamplerDesc', objects)
            start = objects.index('// SamplerBuilder: device@0 then an NvnSamplerDesc at 8')
            samplers = objects[start:objects.index('NVN(nvnSamplerGetDebugID)', start)]
            register = objects[objects.index('NVN(nvnSamplerPoolRegisterSampler)'):]
            register = register[:register.index('}\n') + 2]
            source = out/'sampler.c'
            source.write_text(r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint64_t x[31]; uint64_t v[32][2]; } Cpu;
#define NVN(name) static void hle_##name(Cpu *c)
#define A(i) (c->x[(i)])
#define AP(i) ((uint8_t *)(uintptr_t)c->x[(i)])
#define AF(i) (*(float *)&c->v[(i)])
#define RET(v) (c->x[0] = (uint64_t)(v))
#define RETF(f) do { c->v[0][0] = 0; *(float *)&c->v[0] = (f); } while (0)
#define SET_HOST(guest, p) (*(uint64_t *)(uintptr_t)(guest) = (uint64_t)(uintptr_t)(p))
#define G(T, base, off) (*(T *)((uint8_t *)(uintptr_t)(base) + (off)))
typedef struct {
    uint32_t min_filter, mag_filter, wrap[3], compare_mode, compare_func, reduction;
    float lod_min, lod_max, lod_bias, max_aniso;
    float border[4];
    void *backend;
} NvnSamplerDesc;
typedef struct NvnDescPool { uint64_t addr; int count; void *entries; } NvnDescPool;
#define HOST(T, guest) ((T *)(uintptr_t)(*(uint64_t *)(uintptr_t)(guest)))
''' + samplers + register + r'''
static Cpu cpu;
static uint64_t call3(void (*f)(Cpu *), void *a0, uint64_t a1, uint64_t a2, uint64_t a3) {
    memset(&cpu, 0, sizeof cpu);
    cpu.x[0] = (uint64_t)(uintptr_t)a0; cpu.x[1] = a1; cpu.x[2] = a2; cpu.x[3] = a3;
    f(&cpu);
    return cpu.x[0];
}
#define call1(f, a0, a1, a2) call3(f, a0, a1, a2, 0)
int main(void) {
    _Alignas(16) uint8_t builder[96] = {0}, a[96 + 16], b[96], fresh[96];
    memset(a + 96, 0xAB, 16);  /* guard: nothing past the 96-byte object is written */
    call1(hle_nvnSamplerBuilderSetDefaults, builder, 0, 0);
    call1(hle_nvnSamplerBuilderSetMinMagFilter, builder, 3, 2);
    call3(hle_nvnSamplerBuilderSetWrapMode, builder, 2, 2, 0);
    call1(hle_nvnSamplerInitialize, a, (uint64_t)(uintptr_t)builder, 0);
    for (int i = 96; i < 112; i++) if (a[i] != 0xAB) return 1;
    memcpy(b, a, 96);                          /* the game's wrapper copies the object (0x60 bytes) */
    call1(hle_nvnSamplerFinalize, a, 0, 0);
    memset(a, 0xCD, 96);                       /* the original's memory is reused */
    call1(hle_nvnSamplerInitialize, fresh, (uint64_t)(uintptr_t)builder, 0);
    if (call1(hle_nvnSamplerCompare, b, (uint64_t)(uintptr_t)fresh, 0) != 1) return 2;
    uint32_t min = 0, mag = 0;
    call1(hle_nvnSamplerGetMinMagFilter, b, (uint64_t)(uintptr_t)&min, (uint64_t)(uintptr_t)&mag);
    if (min != 3 || mag != 2) return 3;
    NvnSamplerDesc entries[4] = {{0}};
    NvnDescPool pool = {0, 4, entries};
    uint64_t pool_obj = (uint64_t)(uintptr_t)&pool;
    call1(hle_nvnSamplerPoolRegisterSampler, &pool_obj, 2, (uint64_t)(uintptr_t)b);
    if (entries[2].min_filter != 3 || entries[2].wrap[2] != 0) return 4;
    call1(hle_nvnSamplerBuilderSetMinMagFilter, builder, 1, 1);
    call1(hle_nvnSamplerInitialize, fresh, (uint64_t)(uintptr_t)builder, 0);
    return call1(hle_nvnSamplerCompare, b, (uint64_t)(uintptr_t)fresh, 0) == 0 ? 0 : 5;
}
''', encoding='utf-8')
            exe = out/'sampler.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2',
                                    str(source), '-o', str(exe)], env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn').mkdir(parents=True)
            (runtime/'nvn/objects.c').write_text('int x;\n')
            with self.assertRaises(ValueError):
                sampler_inline.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
