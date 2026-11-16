"""nvnSyncWait must not return before the submission holding its fence was submitted and finished."""
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
import gpu_sync


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class GpuSyncTests(unittest.TestCase):
    def test_display_wait_uses_deadlines_and_keeps_60_hz(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/objects.c').read_text(encoding='utf-8')
            start = text.index('static void display_pace(int interval) {')
            function = text[start:text.index('\n}\n', start) + 3]
            source = out/'display.c'
            source.write_text(r'''
#include <stdlib.h>
#include <stdint.h>
static uint64_t now = 1000000000, deadline;
static unsigned waits;
static uint64_t plat_now_ns(void) { return now; }
static void plat_sleep_until_ns(uint64_t target) { deadline = now = target; waits++; }
''' + function + r'''
int main(int argc, char **argv) {
    uint64_t initial = now, period = 1000000000 / 60;
    display_pace(1);
    if (waits) return 1;
    for (int i = 1; i <= 120; i++) {
        now += 3000123;
        display_pace(1);
        if (argc == 1 && (waits != i || deadline != initial + i * period)) return 2;
    }
    if (argc > 1) return waits != 0; /* existing opt-out */
    display_pace(0);
    if (waits != 120) return 3;
    now += period * 10; /* hitch: reset instead of a burst */
    display_pace(1);
    if (waits != 120) return 4;
    initial = now;
    display_pace(2);
    if (deadline != initial + period) return 5;
    display_pace(1);
    return deadline != initial + 3 * period;
}
''', encoding='utf-8')
            exe = out/'display.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            clean = {k: v for k, v in os.environ.items() if k != 'RECOMP_NO_DISPLAY_PACE'}
            subprocess.run([str(exe)], env=clean, check=True, timeout=30)
            subprocess.run([str(exe), 'off'], env=dict(clean, RECOMP_NO_DISPLAY_PACE='1'), check=True, timeout=30)

    def test_fence_wait_follows_its_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            self.assertNotIn('vk_wait_seq(seq > s ? s : seq)', text)
            self.assertIn('mk8_seq_recording = false;\n    pthread_cond_broadcast(&mk8_seq_cond);', text)
            self.assertIn('mk8_seq_recorder = pthread_self();', text)
            wait = text[text.index('void renderer_wait_seq(uint64_t seq) {'):]
            wait = wait[:wait.index('\n}\n') + 3]
            fixture = r'''
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <windows.h>
#define LOG(...) printf(__VA_ARGS__)
static pthread_mutex_t seq_lock = PTHREAD_MUTEX_INITIALIZER;
static uint64_t submit_seq;
static __thread const char *wait_why;
static volatile uint64_t gpu_done, waited_for, flushes;
static uint64_t plat_now_ns(void) { return (uint64_t)GetTickCount64() * 1000000ull; }
static void vk_wait_seq(uint64_t seq) { waited_for = seq; if (gpu_done < seq) gpu_done = seq; }
static void submit(void) {  /* the adapted submission tail */
    pthread_mutex_lock(&seq_lock);
    submit_seq++;
    mk8_seq_recording = false;
    pthread_cond_broadcast(&mk8_seq_cond);
    pthread_mutex_unlock(&seq_lock);
}
static void renderer_flush(void) { flushes++; submit(); vk_wait_seq(submit_seq); }
static void begin(void) {
    pthread_mutex_lock(&seq_lock);
    mk8_seq_recorder = pthread_self();
    mk8_seq_recording = true;
    pthread_mutex_unlock(&seq_lock);
}
static volatile int recorder_submitted;
static void *recorder(void *arg) {
    (void)arg;
    Sleep(300);  /* still recording while the other thread waits */
    recorder_submitted = 1;
    submit();
    return NULL;
}
'''
            # already submitted: exactly that submission; another thread's open one: after it submits; our own: submit it
            checks = r'''
static void *record_then_submit(void *arg) { (void)arg; begin(); return recorder(NULL); }
int main(void) {
    submit_seq = 4;
    renderer_wait_seq(3);
    if (waited_for != 3 || flushes) return 1;
    pthread_t t;
    pthread_create(&t, NULL, record_then_submit, NULL);
    while (!mk8_seq_recording) Sleep(1);
    renderer_wait_seq(5);
    if (!recorder_submitted || waited_for != 5 || flushes) return 2;
    pthread_join(t, NULL);
    begin();  /* this thread's own open submission: submitted, not waited on forever */
    renderer_wait_seq(6);
    if (flushes != 1 || submit_seq != 6 || waited_for != 6) return 3;
    return 0;
}
'''
            source = out/'sync.c'
            source.write_text(fixture.replace('static pthread_mutex_t seq_lock = PTHREAD_MUTEX_INITIALIZER;\n',
                                              'static pthread_mutex_t seq_lock = PTHREAD_MUTEX_INITIALIZER;\n' + gpu_sync.STATE)
                              + wait + checks, encoding='utf-8')
            exe = out/'sync.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_game_timing_counters_are_not_forged_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            start = text.index('#define PACE_SLOW_NS')
            pacing = text[start:text.index('static void cq_resolve(int i)')]
            fixture = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LOG(...) ((void)0)
static uint64_t now_ns = 1000000000ull;
static uint64_t plat_now_ns(void) { return now_ns; }
__thread uint64_t g_blocked_ns;
static uint64_t ns_to_gpu_ticks(uint64_t ns) { return ns * 384 / 625; }
'''
            checks = r'''
int main(int argc, char **argv) {
    for (int i = 0; i < 100; i++) { now_ns += 30000000ull; vk_pace_frame(); }  /* 30 ms busy frames */
    uint64_t pair[4] = {0, 1000, 0, 2000};  /* a start and an end counter, 16 bytes apart */
    uint64_t end = pace_end_counter(pair, pair + 2, pair[3]);
    int forged = end != pair[3];
    return forged != (argc > 1 && !strcmp(argv[1], "expect-forged"));
}
'''
            source = out/'pace.c'
            source.write_text(fixture + pacing.replace('extern __thread uint64_t g_blocked_ns;', '') + checks, encoding='utf-8')
            exe = out/'pace.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            run_env = {k: v for k, v in os.environ.items() if k != 'LARECOMP_PACE'}
            subprocess.run([str(exe)], check=True, timeout=30, env=run_env)
            subprocess.run([str(exe), 'expect-forged'], check=True, timeout=30, env=dict(run_env, LARECOMP_PACE='1'))

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)/'runtime'
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/vulkan/exec.c').write_text('static pthread_mutex_t seq_lock = PTHREAD_MUTEX_INITIALIZER;\n')
            with self.assertRaises(ValueError):
                gpu_sync.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
