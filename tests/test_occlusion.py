"""SAMPLES_PASSED reports get the cumulative samples at their point, resolved in submission order."""
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
import occlusion


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class OcclusionTests(unittest.TestCase):
    def test_reports_are_cumulative_in_submission_order(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            for needle in ('mk8_oq_resolve_done(done);', 'VK_QUERY_TYPE_OCCLUSION',
                           'vkCmdBeginQuery(qcb, o->pool, o->n++, mk8_occlusion_flags);',
                           'vkCmdBeginQuery(s->main, o->pool, 0, mk8_occlusion_flags);', 'mk8_oq_carry = true;'):
                self.assertIn(needle, text)
            state = text[text.index('// MK8 (toolchain/occlusion.py): SAMPLES_PASSED reports'):text.index('static void cq_resolve_done(uint64_t done) {  // every slot')]
            source = out/'occl.c'
            source.write_text(r'''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <vulkan/vulkan.h>
#define SUBS 8
static struct { VkDevice dev; } g_vk;
static uint64_t results[SUBS][256];
static uint32_t reset_count, reset_calls;
void VKAPI_CALL vkCmdResetQueryPool(VkCommandBuffer cb, VkQueryPool pool, uint32_t first, uint32_t count) {
    (void)cb; (void)pool; (void)first;
    reset_count = count; reset_calls++;
}
static uint64_t plat_now_ns(void) { return 1000; }
static uint64_t ns_to_gpu_ticks(uint64_t ns) { return ns; }
VkResult VKAPI_CALL vkGetQueryPoolResults(VkDevice d, VkQueryPool pool, uint32_t first, uint32_t n, size_t size, void *data,
                                          VkDeviceSize stride, VkQueryResultFlags flags) {
    (void)d; (void)first; (void)size; (void)stride; (void)flags;
    memcpy(data, results[(uintptr_t)pool - 1], n * 8);
    return VK_SUCCESS;
}
''' + state + r'''
int main(void) {
    uint64_t a[3][2] = {{0}}, b[2][2] = {{0}};
    /* slot 5, submitted first: report a0 (no query before), query 0 = 100 samples, report a1, query 1 = 7, report a2,
       query 2 = 5 after the last report */
    Mk8Oq *s5 = &mk8_oq[5], *s2 = &mk8_oq[2];
    s5->pool = (VkQueryPool)(uintptr_t)6; s5->seq = 10; s5->n = 3; s5->nrep = 3;
    s5->dirty = 3;
    results[5][0] = 100; results[5][1] = 7; results[5][2] = 5;
    s5->rep_out[0] = a[0]; s5->rep_after[0] = 0;
    s5->rep_out[1] = a[1]; s5->rep_after[1] = 1;
    s5->rep_out[2] = a[2]; s5->rep_after[2] = 2;
    /* slot 2, submitted after it: query 0 = 3, report b0, query 1 = 0 (hidden), report b1 */
    s2->pool = (VkQueryPool)(uintptr_t)3; s2->seq = 11; s2->n = 2; s2->nrep = 2;
    results[2][0] = 3; results[2][1] = 0;
    s2->rep_out[0] = b[0]; s2->rep_after[0] = 1;
    s2->rep_out[1] = b[1]; s2->rep_after[1] = 2;
    mk8_oq_resolve_done(10);  /* only the first is done */
    if (a[0][0] != 0 || a[1][0] != 100 || a[2][0] != 107 || mk8_samples_total != 112 || b[0][0]) return 1;
    mk8_oq_reset_pool(NULL, s5);
    if (reset_calls != 1 || reset_count != 3 || s5->dirty) return 6;
    mk8_oq_reset_pool(NULL, s5);
    if (reset_calls != 1) return 7;
    mk8_oq_resolve_done(11);
    if (b[0][0] != 115 || b[1][0] != 115 || mk8_samples_total != 115) return 2;  /* b1 - b0 = 0: hidden */
    if (s5->n || s5->nrep || s2->n || s2->nrep) return 3;
    mk8_oq_resolve_done(99);  /* nothing left: no change */
    if (mk8_samples_total != 115) return 4;
    s2->seq = 100; s2->n = 2; s2->nrep = 3;
    results[2][0] = 12; results[2][1] = 6;
    s2->rep_out[0] = a[0]; s2->rep_after[0] = 1;
    s2->rep_out[1] = NULL; s2->rep_after[1] = 1;  /* ordered reset */
    s2->rep_out[2] = a[1]; s2->rep_after[2] = 2;
    mk8_oq_resolve_done(100);
    return a[0][0] == 127 && a[1][0] == 6 && mk8_samples_total == 6 ? 0 : 5;
}
''', encoding='utf-8')
            exe = out/'occl.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', '-I'+str(DEPS/'include'),
                                    str(source), '-o', str(exe)], env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/vulkan/exec.c').write_text('int x;\n')
            (runtime/'nvn/cmdbuf.c').write_text('NVN(nvnCommandBufferResetCounter) { (void)c; }\n')
            with self.assertRaises(ValueError):
                occlusion.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
