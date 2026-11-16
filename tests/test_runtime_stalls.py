"""Stalls seen in a manual session: the pipeline cache is saved during play, and SendSyncRequest frees the core."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import prepare_host_sources
from native import DEFAULT_COMPILER, compiler_env
import ipc_core_release
import pipeline_cache_save
import save_file_close
import texture_release_thread
import vram_trace

HARNESS = r'''
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <windows.h>
#define S 1000000000ull
static _Atomic uint64_t now, sleeps;
static uint64_t saves[16];
static _Atomic int nsaves;
uint64_t plat_now_ns(void) { return atomic_load(&now); }
void mk8_pipeline_built(void);
void plat_sleep_until_ns(uint64_t t) {
    // A fake clock: every poll is 5 s later. Pipelines are built at 100 s and every 5 s from 200 s to 300 s.
    if (atomic_load(&sleeps) >= 90) for (;;) Sleep(1000);
    atomic_store(&now, t);
    if (t == 100 * S || (t >= 200 * S && t <= 300 * S)) mk8_pipeline_built();
    atomic_fetch_add(&sleeps, 1);
}
void vk_pipeline_cache_save(void) { saves[atomic_fetch_add(&nsaves, 1)] = atomic_load(&now) / S; }
void rt_log(const char *fmt, ...) { (void)fmt; }
void mk8_pipeline_saver_start(void);
int main(void) {
    mk8_pipeline_built();  // (one pipeline at 0 s, before the thread starts)
    mk8_pipeline_saver_start();
    mk8_pipeline_saver_start();  // (a second start does nothing)
    while (atomic_load(&sleeps) < 90) Sleep(1);
    for (int i = 0; i < atomic_load(&nsaves); i++) printf("%llu ", (unsigned long long)saves[i]);
    printf("\n");
    return 0;
}
'''

RETIRE = r"""
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#define FATAL(...) (fprintf(stderr, __VA_ARGS__), exit(3))
typedef struct NvnTexture { unsigned mk8_refs; int id; uint64_t mk8_owner; } NvnTexture;
static pthread_t translator;
static _Atomic int destroyed, off_translator;
void backend_texture_destroyed(NvnTexture *t) {
    (void)t;
    atomic_fetch_add(&destroyed, 1);
    if (!pthread_equal(pthread_self(), translator)) atomic_fetch_add(&off_translator, 1);
}
void mk8_texture_detach(NvnTexture *t) { (void)t; }
#include "texture_refs.inc"
static NvnTexture *make(void) { NvnTexture *t = calloc(1, sizeof *t); t->mk8_refs = 1; return t; }
static _Atomic int stop;
static void *translate(void *arg) {
    (void)arg;
    mk8_texture_translator_thread();
    while (!atomic_load(&stop)) mk8_textures_drain();
    mk8_textures_drain();
    return NULL;
}
int main(void) {
    NvnTexture *early = make();
    mk8_texture_release(early);  // (no translator yet: destroyed at once, on this thread)
    if (atomic_load(&destroyed) != 1) return 4;
    atomic_store(&off_translator, 0);
    pthread_create(&translator, NULL, translate, NULL);
    while (!atomic_load(&mk8_translator_running)) ;
    for (int i = 0; i < 20000; i++) {  // the game thread: finalize, or a list still holding a reference
        NvnTexture *t = make();
        if (i % 3 == 0) mk8_texture_retain(t), mk8_texture_release(t);
        mk8_texture_release(t);
    }
    atomic_store(&stop, 1);
    pthread_join(translator, NULL);
    printf("%d %d\n", atomic_load(&destroyed), atomic_load(&off_translator));
    return 0;
}
"""

AMIIBO = r"""
#include <stdio.h>
#include <string.h>
#include "nfp.c"
static Service *made;
static int events;
Service *service_new(const char *name, ServiceHandler h, void *state) {
    made = calloc(1, sizeof *made); made->name = name; made->handler = h; made->state = state; return made; }
void ipc_return_object(IpcResp *resp, Service *s) { (void)resp; (void)s; }
KEvent *kernel_create_event_pair(uint32_t *w, uint32_t *r) { *w = 0x100; *r = 0x200 + events++; return NULL; }
void rt_log(const char *fmt, ...) { (void)fmt; }
void rt_fatal_trace(void) { exit(9); }
static IpcResp call(Service *s, uint32_t cmd) {
    IpcReq req; IpcResp resp; memset(&req, 0, sizeof req); memset(&resp, 0, sizeof resp); req.cmd = cmd;
    if (s) s->handler(s, &req, &resp); else mk8_nfp_user(NULL, &req, &resp);
    return resp;
}
int main(void) {
    call(NULL, 0);                       // CreateUserInterface: an object, not just "ok"
    if (!made || made->handler != mk8_nfp_user_interface || !made->on_close) return 2;
    IpcResp r = call(made, 19);          // GetState before Initialize
    printf("%u ", *(uint32_t *)r.data);
    call(made, 0);                       // Initialize
    r = call(made, 19);
    printf("%u ", *(uint32_t *)r.data);
    r = call(made, 2);                   // ListDevices
    printf("%x %d ", r.result, *(int32_t *)r.data);
    r = call(made, 23);                  // AttachAvailabilityChangeEvent
    printf("%d %x ", r.ncopy, r.copy_handles[0]);
    r = call(made, 3);                   // StartDetection on a device that was never listed
    printf("%x\n", r.result);
    made->on_close(made);
    return made->state != NULL;
}
"""


class RuntimeStallTests(unittest.TestCase):
    def test_prepared_runtime_saves_pipelines_and_frees_the_core_in_ipc(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', Path(folder))
            exec_c = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            self.assertEqual(exec_c.count('mk8_pipeline_built();'), 3)
            device_c = (runtime/'nvn/vulkan/device.c').read_text(encoding='utf-8')
            self.assertIn('static void vk_pipeline_cache_save_locked(void) {', device_c)
            self.assertIn('mk8_pipeline_saver_start();', device_c)
            kernel = (runtime/'kernel.c').read_text(encoding='utf-8')
            dispatch = kernel[kernel.index('case 0x21:'):kernel.index('break;', kernel.index('case 0x21:'))]
            self.assertLess(dispatch.index('kernel_host_wait_begin();'), dispatch.index('ipc_handle_request'))
            self.assertLess(dispatch.index('ipc_handle_request'), dispatch.index('kernel_host_wait_end();'))
            self.assertIn('"inside" : "after"', kernel)
            fs = (runtime/'services/fs.c').read_text(encoding='utf-8')
            self.assertIn('opened->on_close = host_file_close;', fs)
            self.assertIn('opened->on_close = host_dir_close;', fs)
            self.assertIn('close(f->fd);', fs[fs.index('static void host_file_close'):])
            self.assertIn('IFileSystem cmd %u %s -> result', fs)
            objects = (runtime/'nvn/objects.c').read_text(encoding='utf-8')
            self.assertIn('mk8_textures_drain();  //', objects)
            self.assertIn('void mk8_vram_report(void)', exec_c + (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8'))

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            for name in ('nvn/vulkan/exec.c', 'nvn/vulkan/device.c', 'kernel.c'):
                (runtime/name).write_text('int x;\n')
            with self.assertRaises(ValueError):
                pipeline_cache_save.prepare(runtime)
            with self.assertRaises(ValueError):
                ipc_core_release.prepare(runtime)
            (runtime/'services').mkdir()
            (runtime/'services/fs.c').write_text('int y;' + chr(10))
            with self.assertRaises(ValueError):
                save_file_close.prepare(runtime)
            with self.assertRaises(ValueError):
                vram_trace.prepare(runtime)

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_cache_is_saved_after_new_pipelines_once_compiling_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out/'host')
            (out/'harness.c').write_text(HARNESS, encoding='utf-8')
            exe = out/'saver.exe'
            subprocess.run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1', '-I' + str(runtime),
                            out/'harness.c', ROOT/'runtime/mk8/graphics/pipeline_saver.c', '-o', exe],
                           check=True, capture_output=True, env=compiler_env())
            result = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            # (the pipeline built before the thread started counts as saved: the thread starts with the cache)
            # 110 s: 10 s after the one built at 100 s. 310 s: 10 s after the burst of 200-300 s ended, never in the
            # middle of it. Nothing new afterwards, so no other save.
            self.assertEqual(result.stdout.split(), ['110', '310'])

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_textures_retired_on_a_game_thread_are_destroyed_by_the_translator(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out/'retire.c').write_text(RETIRE, encoding='utf-8')
            exe = out/'retire.exe'
            subprocess.run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                            '-I' + str(ROOT/'runtime/mk8/graphics'), out/'retire.c', '-o', exe],
                           check=True, capture_output=True, env=compiler_env())
            result = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            # 1 before the translator started + 20000 afterwards; none of these destroyed off the translator
            self.assertEqual(result.stdout.split(), ['20001', '0'])

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_amiibo_service_returns_an_interface_with_no_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out/'host')
            (out/'amiibo.c').write_text(AMIIBO, encoding='utf-8')
            exe = out/'amiibo.exe'
            built = subprocess.run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1', '-I' + str(runtime),
                                    '-I' + str(ROOT/'runtime/mk8/services'), out/'amiibo.c', '-o', exe],
                                   capture_output=True, text=True, env=compiler_env())
            self.assertEqual(built.returncode, 0, built.stderr[-3000:])
            result = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            # state 0 then 1; ListDevices: DeviceNotFound (module 115, 64) with 0 devices; one event handle;
            # a device command also DeviceNotFound
            self.assertEqual(result.stdout.split(), ['0', '1', '8073', '0', '1', '200', '8073'])


if __name__ == '__main__':
    unittest.main()
