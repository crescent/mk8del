"""Compile-ahead stays off unless LARECOMP_AHEAD asks for it; the samples-passed report is counted."""
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
import shader_ahead


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class ShaderAheadTests(unittest.TestCase):
    def test_hitch_counts_inflight_builds(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            start = text.index('void vk_prof_frame(')
            function = text[start:text.index('\n}\n', start) + 3]
            source = out/'hitch.c'
            source.write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static char message[1024];
#define LOG(...) snprintf(message, sizeof message, __VA_ARGS__)
static int prof = 1;
static uint64_t cum_exec, cum_wait, cum_draws, cum_submits, cum_prefetch, now;
static uint64_t SDL_GetTicksNS(void) { return now; }
static int build_inflight(void) { return 3; }
''' + function + r'''
int main(void) {
    now = 1000000000; vk_prof_frame(1, 0);
    now += 60000000; cum_exec = 10000000; cum_prefetch = 20000000;
    vk_prof_frame(2, 5000000);
    if (!strstr(message, "compiles 3 pending") || !strstr(message, "prefetch 20.0 + present 5.0 + other 25.0")) return 1;
    now += 60000000; vk_prof_frame(3, 0);
    return strstr(message, "prefetch 0.0 + present 0.0 + other 60.0") == NULL;
}
''', encoding='utf-8')
            exe = out/'hitch.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_enable_conditions(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            execute = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            ahead = (runtime/'nvn/vulkan/ahead.c').read_text(encoding='utf-8')
            for relative, old, new in shader_ahead.SWITCHES:
                text = execute if relative.endswith('exec.c') else ahead
                self.assertNotIn(old, text)
                self.assertIn(new, text)
            enable = shader_ahead.SWITCHES[0][2]
            start = shader_ahead.SWITCHES[1][2]
            source = out/'ahead.c'
            source.write_text(r'''
#include <stdlib.h>
#include <string.h>
static int enabled = -1, started;
static void shader_ahead_set_enable(int on) { enabled = on != 0; }
static struct { int started; } vah;
static int atomic_exchange(int *p, int v) { int o = *p; *p = v; return o; }
static const char *g_romfs_dir = "romfs";
static void begin(void) { started = 0;
''' + enable + r'''
}
static void start(void) {
    const char *mode = getenv("LARECOMP_AHEAD");
    vah.started = 0;
''' + start.replace('return;', 'return;\n    started = 1;') + r'''
}
int main(int argc, char **argv) {
    int expect = argc > 1 && !strcmp(argv[1], "on");
    begin(); start();
    return enabled == expect && started == expect ? 0 : 1;
}
''', encoding='utf-8')
            exe = out/'ahead.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            base = {k: v for k, v in os.environ.items() if k != 'LARECOMP_AHEAD'}
            subprocess.run([str(exe)], check=True, env=base, timeout=30)                                    # unset: off
            subprocess.run([str(exe)], check=True, env=dict(base, LARECOMP_AHEAD='0'), timeout=30)          # 0: off
            subprocess.run([str(exe), 'on'], check=True, env=dict(base, LARECOMP_AHEAD='1'), timeout=30)    # 1: on
            self.assertIn('SAMPLES_PASSED (MK8: toolchain/occlusion.py)', execute)


if __name__ == '__main__':
    unittest.main()
