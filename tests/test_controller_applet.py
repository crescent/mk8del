"""The controller applet queues focus out, then in, and GetCurrentFocusState follows the received messages."""
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


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class ControllerAppletTests(unittest.TestCase):
    def test_focus_messages_after_controller_support(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            am = (runtime/'services/am.c').read_text(encoding='utf-8')
            # the real message queue and ICommonStateGetter, from the prepared source
            start = am.index('static uint32_t msg_event_r, msg_event_w;')
            end = am.index('\n}\n', am.index('static void common_state_getter(')) + 3
            self.assertIn('mk8_applet_focus_cycle();', (runtime/'hle/input.c').read_text(encoding='utf-8'))
            source = out/'focus.c'
            source.write_text(r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#define LOG printf
#define MAKE_RESULT(m, d) ((m) | (d) << 9)
#define AM_NO_MESSAGE MAKE_RESULT(128, 3)
#define RT_ON_RESET(name) static void name(void)
typedef struct { bool signaled; } KEvent;
typedef struct { const char *name; } Service;
typedef struct { uint32_t cmd; } IpcReq;
typedef struct { uint8_t data[16]; uint32_t result, copy_handles[4]; int ncopy; } IpcResp;
#define OUT(T, o) (*(T *)(resp->data + (o)))
static void resp_size(IpcResp *r, int n) { (void)r; (void)n; }
pthread_mutex_t g_klock = PTHREAD_MUTEX_INITIALIZER;
static KEvent event;
static void kernel_signal(KEvent *e) { e->signaled = true; }
static KEvent *kernel_create_event_pair(uint32_t *w, uint32_t *r) { *w = 1; *r = 2; return &event; }
''' + am[start:end] + r'''
static IpcResp call(uint32_t cmd) {
    IpcReq req = {cmd}; IpcResp resp = {0};
    common_state_getter(NULL, &req, &resp);
    return resp;
}
static int fail(int code) { printf("failed %d\n", code); return code; }
int main(void) {
    am_reset();
    mk8_applet_focus_cycle();  // before the game asked for messages: nothing queued
    am_init_once();
    IpcResp r = call(1);
    if (r.result || *(uint32_t *)r.data != 15) return fail(1);  // the boot message
    if (call(9).data[0] != 1 || event.signaled) return fail(2);
    mk8_applet_focus_cycle();
    if (!event.signaled) return fail(3);
    r = call(1);
    if (r.result || *(uint32_t *)r.data != 15 || call(9).data[0] != 2) return fail(4);  // out of focus
    r = call(1);
    if (r.result || *(uint32_t *)r.data != 15 || call(9).data[0] != 1) return fail(5);  // back in focus
    if (call(1).result != AM_NO_MESSAGE || event.signaled) return fail(6);
    for (int i = 0; i < 20; i++) mk8_applet_focus_cycle();  // a full queue drops the pair, never overflows
    if (msg_count > 16 || msg_count % 2) return fail(7);
    am_reset();
    if (call(9).data[0] != 1) return fail(8);
    return 0;
}
''', encoding='utf-8')
            exe = out/'focus.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', str(source), '-o', str(exe)],
                           env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
