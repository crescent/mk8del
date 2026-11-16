"""Async translation: queued work runs in order, fences wait for their own ticket, presents stay one frame ahead."""
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
import async_submit


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class AsyncSubmitTests(unittest.TestCase):
    def test_queue_order_fences_and_throttle(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            objects = (runtime/'nvn/objects.c').read_text(encoding='utf-8')
            start = objects.index('// MK8 (toolchain/async_submit.py): submissions, presents')
            queue = objects[start:objects.index('void renderer_wait_seq(uint64_t seq);', start)]
            source = out/'queue.c'
            source.write_text(r'''
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "nvn/nvn.h"
void rt_log(const char *fmt, ...) { (void)fmt; }
void rt_fatal_trace(void) { abort(); }
static NvnSync syncs[4];
static NvnSync *sync_of(uint64_t guest) { return &syncs[guest - 1]; }
static char order[64];
static int norder;
static volatile LONG gate = 1;  /* the translator stalls in a present while 0 */
static uint64_t seq;
static unsigned releases;
int mk8_textures_release_range(uint64_t lo, uint64_t hi, uint64_t *bytes) {
    if (lo != 0x1000 || hi != 0x2000) abort();
    *bytes = 4096; releases++; order[norder++] = 'R'; return 1;
}
void backend_prefetch(const uint64_t *h, int n) { (void)h; (void)n; }
void backend_execute(uint64_t h) { order[norder++] = (char)('a' + ((CmdRec *)(uintptr_t)h)->x[6]); seq++; }
static int drains, translator_marked;  // (toolchain/texture_release_thread.py)
void mk8_texture_translator_thread(void) { translator_marked++; }
void mk8_textures_drain(void) { drains++; }
void renderer_prefetch_verify(void) {}
uint64_t renderer_fence_seq(void) { return seq; }
void renderer_submit(void) {}
static void mk8_present_now(NvnTexture *t) { (void)t; while (!gate) Sleep(1); order[norder++] = 'P'; }
''' + queue.replace('static void mk8_present_now(NvnTexture *t);  // (below, with nvnQueuePresentTexture)\n', '') + r'''
static _Alignas(8) uint8_t list_a[1024], list_b[1024], list_c[1024];
static uint8_t *put(uint8_t *p, uint32_t op, uint64_t x0, uint64_t x1, uint64_t tag) {
    CmdRec *r = (CmdRec *)p;
    memset(r, 0, sizeof *r);
    r->op = op; r->size = sizeof(CmdRec); r->x[0] = x0; r->x[1] = x1; r->x[6] = tag;
    return p + r->size;
}
int main(int argc, char **argv) {
    if (argc > 1) {
        if (!strcmp(argv[1], "overflow")) { mk8_pool_released(UINT64_MAX, 2); return 99; }
        mk8_pool_released(0x1000, 0);  // zero-sized pool does nothing
        mk8_pool_released(0x1000, 0x1000);
        const char *enabled = getenv("MK8_RELEASE_POOL_TEXTURES");
        unsigned expected = enabled && atoi(enabled) != 0 && !getenv("MK8_KEEP_POOL_TEXTURES");
        return releases == expected ? 0 : 98;
    }
    /* list a: a fence for sync 1, then a call into list c, which jumps to b's fence for sync 2 */
    uint8_t *p = put(list_a, CMD_FENCE_SYNC, 0, 1, 0);
    CmdRec *call = (CmdRec *)p;
    p = put(p, CMD_CALL, 1, 0, 0);
    call->size += 8;
    *(uint64_t *)call->extra = (uint64_t)(uintptr_t)list_c;
    p += 8;
    put(p, CMD_END, 0, 0, 0);
    p = put(list_c, CMD_JUMP, (uint64_t)(uintptr_t)list_b, 0, 0);
    p = put(list_b, CMD_FENCE_SYNC, 0, 2, 0);
    put(p, CMD_END, 0, 0, 0);
    ((CmdRec *)list_a)->x[6] = 0;  /* executes as 'a' */
    ((CmdRec *)list_b)->x[6] = 1;
    if (!mk8_async_on()) return 1;  /* on by default */
    gate = 0;  /* the translator will stall in the first present */
    Mk8QItem *pr = calloc(1, sizeof *pr); pr->kind = MK8_Q_PRESENT; mk8_q_put(pr, NULL, 0);
    uint64_t h[2] = {(uint64_t)(uintptr_t)list_a, (uint64_t)(uintptr_t)list_b};
    if (!mk8_async_submit(2, h)) return 2;
    if (syncs[0].mk8_ticket != 2 || syncs[1].mk8_ticket != 2) return 3;  /* both fences tagged with the submission */
    mk8_pool_released(0x1000, 0x1000);
    if (!mk8_async_submit(1, h + 1)) return 8;  /* storage reused after retirement */
    Mk8QItem *fe = calloc(1, sizeof *fe); fe->kind = MK8_Q_FENCE; fe->sync = &syncs[2];
    NvnSync *s3 = &syncs[2];
    mk8_q_put(fe, &s3, 1);
    if (syncs[2].mk8_ticket != 5 || mk8_q_presents != 1) return 4;
    Sleep(30);
    if (norder != 0 || mk8_q_done != 0 || releases) return 5;  /* retirement cannot pass older commands */
    gate = 1;
    mk8_q_wait(syncs[2].mk8_ticket);
    if (syncs[2].seq != 3) return 6;
    mk8_q_drain();
    return norder == 5 && !memcmp(order, "PabRb", 5) && releases == 1 && mk8_q_presents == 0 ? 0 : 7;
}
''', encoding='utf-8')
            exe = out/'queue.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            env.pop('MK8_ASYNC_SUBMIT', None)
            env.pop('MK8_KEEP_POOL_TEXTURES', None)
            env.pop('MK8_RELEASE_POOL_TEXTURES', None)
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O2', '-DFATAL(...)=abort()',
                                    '-I'+str(runtime), '-I'+str(runtime/'nvn'), '-I'+str(DEPS/'include'), str(source), '-o', str(exe)],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe), 'default'], check=True, timeout=30, env=env)
            subprocess.run([str(exe), 'disabled'], check=True, timeout=30,
                           env=dict(env, MK8_RELEASE_POOL_TEXTURES='0'))
            env['MK8_RELEASE_POOL_TEXTURES'] = '1'
            subprocess.run([str(exe)], check=True, timeout=30, env=env)
            subprocess.run([str(exe), 'sync'], check=True, timeout=30, env=dict(env, MK8_ASYNC_SUBMIT='0'))
            subprocess.run([str(exe), 'keep'], check=True, timeout=30,
                           env=dict(env, MK8_ASYNC_SUBMIT='0', MK8_KEEP_POOL_TEXTURES='1'))
            result = subprocess.run([str(exe), 'overflow'], timeout=30, env=env, capture_output=True)
            self.assertNotIn(result.returncode, (0, 99))

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn').mkdir(parents=True)
            (runtime/'nvn/nvn.h').write_text('int x;\n')
            (runtime/'nvn/objects.c').write_text('int y;\n')
            with self.assertRaises(ValueError):
                async_submit.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
