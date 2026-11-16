"""A dispatch's results are waited for only when a later CPU copy reads memory it writes."""
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
import compute_flush


def between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class ComputeFlushTests(unittest.TestCase):
    def test_submit_preserves_pending_reads_without_cpu_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            source = out/'submit.c'
            source.write_text(r'''
#include <stdint.h>
#include <stdlib.h>
#define LOG(...) ((void)0)
static int no_flush, cb, pending, barriers, submits, waits;
static const char *wait_why;
static unsigned long long mk8_cw_deferred, mk8_cw_flushes, mk8_cw_missed;
static struct { struct { uint64_t addr, size; } sbuf[6][16]; } st;
static struct { int uses_gmem_store; } si = {1};
static struct { void *unused; typeof(si) *si; } pipeline = {0, &si};
static typeof(pipeline) *cp = &pipeline;
EAGER_FUNCTION
static void vk_full_barrier(int b) { (void)b; barriers++; }
static void mk8_cw_mark(uint64_t a, uint64_t s) { if (a && s) pending = 1; }
static void renderer_submit(void) { if (!pending || !barriers) abort(); submits++; }
static void renderer_flush(void) { waits++; pending = 0; }
static void finish_dispatch(void) {
'''.replace('EAGER_FUNCTION', between(compute_flush.READERS, 'static int mk8_eager_compute', 'static void mk8_cw_flush')) + compute_flush.NEW_FLUSH + r'''
int main(int argc, char **argv) {
    int mode = atoi(argv[1]);
    st.sbuf[5][0].addr = 4096; st.sbuf[5][0].size = 256;
    finish_dispatch();
    if (!mode) return waits != 1 || pending || submits;
    if (waits || !pending || barriers != 1 || submits != (mode == 2)) return 2;
    renderer_flush(); /* a later CPU reader still has to wait */
    if (pending || waits != 1) return 3;
    si.uses_gmem_store = 0;
    finish_dispatch();
    if (pending || waits != 1 || submits != (mode == 2)) return 4;
    si.uses_gmem_store = 1;
    no_flush = 1;
    finish_dispatch();
    return pending || waits != 1 || submits != (mode == 2);
}
''', encoding='utf-8')
            exe = out/'submit.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            clean = {k: v for k, v in os.environ.items() if k not in ('MK8_DEFER_COMPUTE_FLUSH', 'MK8_COMPUTE_SUBMIT')}
            for mode, options in [(2, {}), (0, {'MK8_DEFER_COMPUTE_FLUSH': '0'}),
                                  (2, {'MK8_DEFER_COMPUTE_FLUSH': '1'}),
                                  (1, {'MK8_DEFER_COMPUTE_FLUSH': '1', 'MK8_COMPUTE_SUBMIT': '0'}),
                                  (2, {'MK8_DEFER_COMPUTE_FLUSH': '1', 'MK8_COMPUTE_SUBMIT': '1'})]:
                subprocess.run([str(exe), str(mode)], env=dict(clean, **options), check=True, timeout=30)

    def test_cpu_copies_wait_only_for_overlapping_dispatches(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            self.assertNotIn('{ wait_why = "compute results"; renderer_flush(); }\n}', text.replace('if (mk8_eager_compute()) ', ''))
            self.assertIn('    mk8_cw_before_draw(r, indexed);\n', text)
            track = between(text, '// MK8 (toolchain/compute_flush.py): guest memory written', 'static void gpu_read_clear(void) {')
            readers = between(text, '// MK8: CPU copies at a command boundary', 'static void dispatch_compute(')
            draw = between(text, '// Vertex streams and indices the draw binds in place', 'static void draw(CmdRec *r, bool indexed) {')
            fixture = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>
#define HEAP_BASE 0x10000000ull
#define HEAP_MAX (64ull << 20)
#define GPU_READ_PAGE_SHIFT 16
static struct { VkPhysicalDeviceProperties props; } g_vk;
static VkBuffer gmem_buf = (VkBuffer)(uintptr_t)0x1234;
static uint64_t gmem_size = 64ull << 20;
static void gpu_read_mark(uint64_t a, uint64_t s) { (void)a; (void)s; }
static uint64_t gmem_direct_limit(void) { return 64ull << 20; }
typedef struct { uint64_t addr, size; } Range;
typedef struct { uint64_t x[8]; } CmdRec;
static struct { Range vbuf[16], ubuf[6][18], sbuf[6][16]; } st;
static __thread const char *wait_why;
static int flushes;
static void mk8_cw_clear(void);
static void renderer_flush(void) { flushes++; mk8_cw_clear(); }
'''
            checks = r'''
int main(void) {
    g_vk.props.limits.minUniformBufferOffsetAlignment = 256;
    g_vk.props.limits.maxUniformBufferRange = 65536;
    mk8_cw_mark(HEAP_BASE + 0x200000, 0x1000);  /* a dispatch's storage buffer */
    CmdRec r = {{0}};
    st.ubuf[0][3] = (Range){HEAP_BASE + 0x800000, 0x100};  /* elsewhere: no wait */
    mk8_cw_before_draw(&r, false);
    if (flushes || !mk8_cw_any) return 1;
    st.ubuf[1][2] = (Range){HEAP_BASE + 0x200800, 0x100};  /* aligned, inside it: read in place, no wait */
    mk8_cw_before_draw(&r, false);
    VkDescriptorBufferInfo d;
    if (flushes || !mk8_cw_any || !mk8_ubo_direct(HEAP_BASE + 0x200800, 0x100, &d) || d.buffer != gmem_buf || d.offset != 0x200800 ||
        d.range != 0x100) return 8;
    if (mk8_ubo_direct(HEAP_BASE + 0x800000, 0x100, &d)) return 9;  /* not written by a dispatch: copied as before */
    st.ubuf[1][2] = (Range){HEAP_BASE + 0x200810, 0x100};  /* inside it but not aligned for a descriptor: wait */
    mk8_cw_before_draw(&r, false);
    if (flushes != 1 || mk8_cw_any) return 2;
    mk8_cw_before_draw(&r, false);  /* finished: nothing pending, no second wait */
    if (flushes != 1) return 3;
    memset(&st, 0, sizeof st);
    mk8_cw_mark(HEAP_BASE + 0x300000, 0x40);
    r.x[2] = 3; r.x[3] = HEAP_BASE + 0x300010;  /* indices written by the dispatch */
    mk8_cw_before_draw(&r, false);
    if (flushes != 1) return 4;  /* (not indexed: the index range is not read) */
    mk8_cw_before_draw(&r, true);
    if (flushes != 2) return 5;
    mk8_cw_mark(HEAP_BASE + 0x400000, 0x40);
    st.ubuf[5][0] = (Range){HEAP_BASE + 0x400020, 0x10};  /* a dispatch reading the previous one's results */
    if (!mk8_cw_stage_pending(5) || mk8_cw_stage_pending(0)) return 6;
    memset(&st, 0, sizeof st); mk8_cw_clear();
    mk8_cw_mark(HEAP_BASE + 0x500000, 0x1000);  /* vertices written by a dispatch, bound in place: no wait */
    st.vbuf[0] = (Range){HEAP_BASE + 0x500000, 0x800};
    int before = flushes;
    mk8_cw_before_draw(&r, false);
    if (flushes != before || mk8_cw_why[1]) return 10;
    st.vbuf[0].size = 0;  /* unsized: copied on the CPU, so it waits */
    mk8_cw_before_draw(&r, false);
    if (flushes != before + 1 || mk8_cw_why[1] != 1) return 11;
    if (mk8_cw_pending(0x1000, 0x10) || mk8_cw_pending(HEAP_BASE + HEAP_MAX, 0x10)) return 7;  /* outside the heap */
    return 0;
}
'''
            source = out/'cw.c'
            source.write_text(fixture + track + readers + draw + checks, encoding='utf-8')
            exe = out/'cw.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', '-I'+str(DEPS/'include'),
                                    str(source), '-o', str(exe)], env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_report_value_waits_for_pending_dispatches(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            case = between(text, '        case CMD_REPORT_VALUE: {', '        case CMD_CLEAR_BUFFER: {')
            track = between(text, '// MK8 (toolchain/compute_flush.py): guest memory written', 'static void gpu_read_clear(void) {')
            source = out/'report.c'
            source.write_text(r'''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define HEAP_BASE 0x10000000ull
#define HEAP_MAX (64ull << 20)
#define GPU_READ_PAGE_SHIFT 16
enum { CMD_REPORT_VALUE = 1, CMD_CLEAR_BUFFER = 2 };
typedef struct { uint32_t op; uint64_t x[8]; } CmdRec;
static __thread const char *wait_why;
static int flushes, written_after_flush;
static uint64_t report[2];
static void mk8_cw_clear(void);
static void mk8_cw_flush(const char *why) { (void)why; flushes++; mk8_cw_clear(); }
static uint64_t plat_now_ns(void) { return 1000; }
static uint64_t ns_to_gpu_ticks(uint64_t ns) { return ns; }
''' + track + r'''
static void run(CmdRec *r) {
    switch (r->op) {
''' + case + r'''
    default: break;
    }
}
int main(void) {
    CmdRec r = {CMD_REPORT_VALUE, {7, (uint64_t)(uintptr_t)report}};
    run(&r);  /* nothing pending: written, no wait */
    if (flushes || report[0] != 7) return 1;
    mk8_cw_mark(HEAP_BASE + 0x1000, 0x100);  /* a dispatch still running */
    r.x[0] = 8;
    run(&r);
    if (flushes != 1 || report[0] != 8 || mk8_cw_any || mk8_cw_reports != 1) return 2;
    return 0;
}
''', encoding='utf-8')
            exe = out/'report.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', str(source), '-o', str(exe)],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/vulkan/exec.c').write_text('static void gpu_read_clear(void) {\n}\n')
            with self.assertRaises(ValueError):
                compute_flush.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
