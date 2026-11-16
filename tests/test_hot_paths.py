"""pipe_key reads its debug switch once, not on every draw, and keeps its meaning."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import prepare_host_sources
from native import DEFAULT_COMPILER
import hot_paths


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class HotPathTests(unittest.TestCase):
    def test_shader_append_matches_original_across_growth(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            def append(text):
                start = text.index('static void sp(Str *s, const char *fmt, ...) {')
                return text[start:text.index('\n}\n', start) + 3]
            old = append((ROOT/'upstream/smm2recomp/recomp/runtime/nvn/maxwell/translate.c').read_text())
            new = append((runtime/'nvn/maxwell/translate.c').read_text())
            source = out/'append.c'
            source.write_text(r'''
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
typedef struct { char *buf; size_t len, cap; } Str;
static int calls;
static int counted(char *out, size_t size, const char *fmt, va_list ap) {
    calls++; return vsnprintf(out, size, fmt, ap);
}
#define vsnprintf counted
''' + old.replace('void sp(', 'void reference(') + new + r'''
int main(void) {
    Str a = {0}, b = {0};
    sp(&a, ""); reference(&b, "");
    for (int i = 0; i < 10000; i++) {
        sp(&a, "r%d = %s(%08x, %.8f);\n", i, "fma", i * 17, i / 7.0);
        reference(&b, "r%d = %s(%08x, %.8f);\n", i, "fma", i * 17, i / 7.0);
        if (a.len != b.len || strcmp(a.buf, b.buf)) return 1;
    }
    sp(&a, "%010000d", 7); reference(&b, "%010000d", 7);
    if (a.len != b.len || strcmp(a.buf, b.buf)) return 2;
    if (a.cap - a.len < 2) return 3;
    calls = 0; sp(&a, "x");
    if (calls != 1) return 4;
    free(a.buf); free(b.buf);
    return 0;
}
''', encoding='utf-8')
            exe = out/'append.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_fmz_mode_is_cached_per_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            source = out/'fmz.c'
            source.write_text(r'''
#include <stdlib.h>
#include <pthread.h>
static __thread int reads;
static char *counted_getenv(const char *key) { reads++; return getenv(key); }
#define getenv counted_getenv
''' + hot_paths.FMZ_MODE + r'''
static void *check(void *arg) {
    int expected = *(int *)arg;
    for (int i = 0; i < 100000; i++) if (mk8_fmz_mode() != expected) abort();
    if (reads != 1) abort();
    return NULL;
}
int main(int argc, char **argv) {
    int expected = argc > 1 ? -1 : 2;
    pthread_t worker;
    pthread_create(&worker, NULL, check, &expected);
    check(&expected);
    pthread_join(worker, NULL);
    return 0;
}
''', encoding='utf-8')
            exe = out/'fmz.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            clean = {k: v for k, v in os.environ.items() if k != 'LARECOMP_NO_FMZ'}
            subprocess.run([str(exe)], env=clean, check=True, timeout=30)
            for value in ('0', '1'):
                subprocess.run([str(exe), 'off'], env=dict(clean, LARECOMP_NO_FMZ=value), check=True, timeout=30)

    def test_switch_is_read_once_and_still_applies(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            for old, _ in hot_paths.SWITCHES:
                self.assertNotIn(old, text)
            textures = (runtime/'nvn/vulkan/textures.c').read_text(encoding='utf-8')
            for old, _ in hot_paths.TEXTURE_SWITCHES:
                self.assertNotIn(old, textures)
            # (MK8's own diagnostics: no per-call getenv left in the per-draw render-target layout or audio mix)
            self.assertNotIn('if (getenv("MK8_TRACE_TEXTURES"))', text)
            self.assertNotIn('!getenv("LARECOMP_AUDIO_LOG")', (runtime/'services/audio.c').read_text(encoding='utf-8'))
            block = text[text.index('    static int no_blend = -1;'):]
            block = block[:block.index('k->blend_on = 0;') + len('k->blend_on = 0;')]
            source = out/'blend.c'
            source.write_text(r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static int reads;
static char *counted_getenv(const char *n) { reads++; return getenv(n); }
#define getenv counted_getenv
typedef struct { uint8_t blend_on; } Key;
static void key(Key *k) {
''' + block + r'''
}
int main(int argc, char **argv) {
    Key k;
    for (int i = 0; i < 1000; i++) { k.blend_on = 3; key(&k); }
    int expect_off = argc > 1 && !strcmp(argv[1], "off");
    if (reads != 1) return 1;
    return k.blend_on == (expect_off ? 0 : 3) ? 0 : 2;
}
''', encoding='utf-8')
            exe = out/'blend.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            base = {k: v for k, v in os.environ.items() if k != 'LARECOMP_VK_NO_BLEND'}
            subprocess.run([str(exe)], check=True, env=base, timeout=30)
            subprocess.run([str(exe), 'off'], check=True, env=dict(base, LARECOMP_VK_NO_BLEND='1'), timeout=30)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/vulkan/exec.c').write_text('int x;\n')
            with self.assertRaises(ValueError):
                hot_paths.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
