"""Bounded shader waits: a running build is waited for at most the frame's budget, never run inline."""
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
import shader_budget


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class ShaderBudgetTests(unittest.TestCase):
    def test_budget_bounds_waits_per_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            execute = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            for _, new in shader_budget.SITES:
                self.assertIn(new, execute)
            budget = execute[execute.index('// MK8 (toolchain/shader_budget.py): MK8_SHADER_WAIT_MS'):
                             execute.index('static bool prog_ready(Prog *p) {')]
            source = out/'budget.c'
            source.write_text(r'''
#define LOG(...) ((void)0)
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <windows.h>
#include "buildsync.h"
#include "nvn/nvn.h"
#include "nvn/vulkan/vk.h"
void rt_log(const char *fmt, ...) { (void)fmt; }
/* the draw's targets: a screen-sized one (redrawn each frame) or a 32x32 probe cube (drawn once) */
static TexBackend screen_b = {.scalable = true}, probe_b = {.scalable = false};
static NvnTexture screen_t, probe_t;
static struct { struct { int ncolor; NvnTexture *color[8]; NvnTexture *depth; } rt; } st;
static void target(NvnTexture *t) { st.rt.ncolor = 1; st.rt.color[0] = t; }
static uint64_t SDL_GetTicksNS(void) {
    LARGE_INTEGER f, t; QueryPerformanceFrequency(&f); QueryPerformanceCounter(&t);
    return (uint64_t)((double)t.QuadPart * 1e9 / (double)f.QuadPart);
}
static uint64_t frame_no;
uint64_t vk_frame_counter(void) { return frame_no; }
static void slow(void *arg) { Sleep((DWORD)(uintptr_t)arg); }
''' + budget + r'''
int main(int argc, char **argv) {
    (void)argv;
    screen_t.backend = &screen_b; probe_t.backend = &probe_b;
    target(&screen_t);
    if (argc > 1 && !strcmp(argv[1], "default")) return mk8_shader_budget_ms() == 4 ? 0 : 11;  /* unset: 4 ms */
    if (argc > 1) {  /* negative budget: the upstream wait runs a queued job inline and returns ready */
        JobQueue *q = jobq_create(1);
        BuildJob a = {0}, b = {0};
        jobq_submit(q, &a, slow, (void *)(uintptr_t)60);
        Sleep(10);
        jobq_submit(q, &b, slow, (void *)(uintptr_t)1);
        return mk8_job_ready(q, &b) && atomic_load(&b.state) == JOB_DONE ? 0 : 10;
    }
    JobQueue *q = jobq_create(1);
    BuildJob a = {0}, b = {0};
    jobq_submit(q, &a, slow, (void *)(uintptr_t)120);  /* the worker is busy with a for 120 ms */
    Sleep(10);
    jobq_submit(q, &b, slow, (void *)(uintptr_t)1);    /* b waits in the queue behind it */
    uint64_t t0 = SDL_GetTicksNS();
    if (mk8_job_ready(q, &a)) return 1;                 /* 8 ms budget: a is still running */
    double ms = (SDL_GetTicksNS() - t0) / 1e6;
    if (ms < 5 || ms > 40) return 2;
    t0 = SDL_GetTicksNS();
    if (mk8_job_ready(q, &b)) return 3;                 /* same frame: the budget is spent */
    if ((SDL_GetTicksNS() - t0) / 1e6 > 5) return 4;
    if (atomic_load(&b.state) != JOB_QUEUED) return 5;  /* never run inline by the draw */
    frame_no++;                                         /* the next frame has its own budget */
    Sleep(150);                                         /* both finish on the worker meanwhile */
    if (!mk8_job_ready(q, &a) || !mk8_job_ready(q, &b)) return 6;
    if (mk8_sb_skips != 2) return 7;
    /* a draw into a probe (drawn once) is never skipped: it waits for its build in full */
    BuildJob c = {0};
    jobq_submit(q, &c, slow, (void *)(uintptr_t)60);
    target(&probe_t);
    frame_no++;
    if (!mk8_job_ready(q, &c) || atomic_load(&c.state) != JOB_DONE) return 8;
    /* nor one whose target has no image yet */
    BuildJob d = {0};
    jobq_submit(q, &d, slow, (void *)(uintptr_t)60);
    screen_t.backend = NULL; target(&screen_t);
    frame_no++;
    if (!mk8_job_ready(q, &d) || atomic_load(&d.state) != JOB_DONE) return 9;
    return mk8_sb_skips == 2 ? 0 : 10;
}
''', encoding='utf-8')
            exe = out/'budget.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2',
                                    '-DLOG(...)=((void)0)', '-I'+str(runtime/'nvn'), '-I'+str(runtime), '-I'+str(DEPS/'include'),
                                    str(source), str(runtime/'nvn/buildsync.c'), '-o', str(exe)],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe)], check=True, timeout=30,
                           env=dict(os.environ, MK8_SHADER_WAIT_MS='8'))
            subprocess.run([str(exe), 'off'], check=True, timeout=30, env=dict(os.environ, MK8_SHADER_WAIT_MS='-1'))
            unset = {k: v for k, v in os.environ.items() if k != 'MK8_SHADER_WAIT_MS'}
            subprocess.run([str(exe), 'default'], check=True, timeout=30, env=unset)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/buildsync.c').write_text('int x;\n')
            (runtime/'nvn/vulkan/exec.c').write_text('int y;\n')
            with self.assertRaises(ValueError):
                shader_budget.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
