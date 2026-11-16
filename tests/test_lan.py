"""Run the prepared LAN services: interface availability and blocking receive scheduling."""
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

STUB = r'''
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <pthread.h>
#include <assert.h>
typedef struct { uint64_t addr; size_t size; } IpcBuf;
typedef struct { unsigned cmd, data_size; uint8_t data[64]; IpcBuf in[3], out[3]; } IpcReq;
typedef struct { uint8_t data[512]; unsigned result, size, ncopy, copy_handles[4]; } IpcResp;
typedef struct { void *state; } Service;
typedef struct { int unused; } KEvent;
static pthread_mutex_t g_klock = PTHREAD_MUTEX_INITIALIZER;
#define IN(T, n) (*(T *)(req->data+(n)))
#define OUT(T, n) (*(T *)(resp->data+(n)))
#define LOG(...) ((void)0)
#define MAKE_RESULT(a,b) ((a) | ((b)<<9))
#define SERVICE(name, fn) void fn(Service *s, IpcReq *req, IpcResp *resp)
static void resp_size(IpcResp *r, unsigned n) { r->size = n; }
static IpcBuf *in_buf(IpcReq *r, int n) { return &r->in[n]; }
static IpcBuf *out_buf(IpcReq *r, int n) { return &r->out[n]; }
static void ipc_return_object(IpcResp *r, Service *s) { (void)r; (void)s; }
static Service *service_new(const char *name, void (*fn)(Service*,IpcReq*,IpcResp*), void *state) {
    static Service s; (void)name; (void)fn; s.state=state; return &s;
}
static KEvent *kernel_create_event_pair(uint32_t *w, uint32_t *r) {
    static KEvent e; *w=1; *r=2; return &e;
}
static void kernel_signal(KEvent *e) { (void)e; }
void kernel_host_wait_begin(void);
void kernel_host_wait_end(void);
static bool active = true;
bool vlan_active(void) { return active; }
uint32_t vlan_ip(void) { return 0x0a0d2501; }
bool net_online(void) { return false; }
bool net_real_sockets(void) { return false; }
void online_offline_notice(void) {}
void bsd_host_client(IpcReq *req, IpcResp *resp) { (void)req; (void)resp; assert(0); }
void vlan_set_receiver(void (*f)(uint32_t,uint16_t,uint16_t,const uint8_t*,size_t)) { (void)f; }
int vlan_send(uint16_t s, uint32_t ip, uint16_t d, const void *p, size_t n) {
    (void)s; (void)ip; (void)d; (void)p; return (int)n;
}
'''


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class LanTests(unittest.TestCase):
    def test_csrng_fills_real_distinct_bytes_and_rejects_invalid_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out/'hle.h').write_text(STUB, encoding='utf-8')
            (out/'ipc.h').write_text('#include "hle.h"', encoding='utf-8')
            source = out/'test.c'
            source.write_text((ROOT/'runtime/mk8/services/csrng.c').read_text(encoding='utf-8')+r'''
#include <string.h>
int main(void) {
    uint8_t a[32]={0}, b[32]={0}, zero[32]={0};
    Service svc={0};IpcReq req={0};IpcResp resp={0};
    req.out[0]=(IpcBuf){(uintptr_t)a,sizeof a};mk8_csrng(&svc,&req,&resp);assert(!resp.result);
    req.out[0].addr=(uintptr_t)b;mk8_csrng(&svc,&req,&resp);assert(!resp.result);
    assert(memcmp(a,zero,32) && memcmp(b,zero,32) && memcmp(a,b,32));
    req.cmd=1;mk8_csrng(&svc,&req,&resp);assert(resp.result);
    req.cmd=0;resp.result=0;req.out[0].size=1024*1024+1;
    mk8_csrng(&svc,&req,&resp);assert(resp.result);
    return 0;
}
''', encoding='utf-8')
            exe = out/'rng.exe'
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                            str(source), '-o', str(exe), '-lbcrypt', '-lpthread'],
                env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache')), check=True)
            subprocess.run([str(exe)], check=True, timeout=10)

    def test_interface_and_receive_release_the_guest_core(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            platform = (runtime/'platform/windows.c').read_text(encoding='utf-8')
            start = platform.index('void plat_exec(char *const argv[]) {')
            function = platform[start:platform.index('\n}\n', start)+3]
            source = out/'retry.c'
            source.write_text(r'''
#include <windows.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
static bool g_console_freed;
static void append_arg(char *out, size_t cap, const char *arg) { (void)out;(void)cap;(void)arg;abort(); }
''' + function + r'''
int main(void) {
    if (getenv("MK8_RETRY_CHILD")) { Sleep(100); return 37; }
    SetEnvironmentVariableA("MK8_RETRY_CHILD", "1");
    plat_exec(NULL);
    return 99;
}
''', encoding='utf-8')
            exe = out/'retry.exe'
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                            str(source), '-o', str(exe)],
                env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache')), check=True)
            clean = {k:v for k,v in os.environ.items() if k != 'MK8_RETRY_CHILD'}
            self.assertEqual(subprocess.run([str(exe)], env=clean, timeout=10).returncode, 37)
            (runtime/'hle.h').write_text(STUB, encoding='utf-8')
            (runtime/'ipc.h').write_text('#include "hle.h"', encoding='utf-8')
            (runtime/'net/netcfg.h').write_text('#pragma once\n#include "../hle.h"', encoding='utf-8')
            (runtime/'net/online.h').write_text('#pragma once\n#include "../hle.h"', encoding='utf-8')
            for name, check in {
                'nifm': r'''
void kernel_host_wait_begin(void) {}
void kernel_host_wait_end(void) {}
int main(void) {
    NifmRequest state = {0}; Service svc = {&state}; IpcReq req = {0}; IpcResp resp = {0};
    request(&svc, &req, &resp);
    assert(*(uint32_t*)resp.data == REQUEST_AVAILABLE);
    req.cmd=18; general(&svc,&req,&resp);
    assert(!resp.result && resp.size==3 && resp.data[2]==4);
    req.cmd=12; general(&svc,&req,&resp);
    assert(*(uint32_t*)resp.data==0x01250d0a);
    active=false; req.cmd=0; request(&svc,&req,&resp);
    assert(*(uint32_t*)resp.data==REQUEST_ERROR);
    req.cmd=18; general(&svc,&req,&resp); assert(resp.result==NIFM_NO_INTERNET);
    return 0;
}
''',
                'bsd': r'''
static pthread_mutex_t core = PTHREAD_MUTEX_INITIALIZER;
static int begins, ends;
void kernel_host_wait_begin(void) { begins++; pthread_mutex_unlock(&core); }
void kernel_host_wait_end(void) {
    assert(pthread_mutex_trylock(&lock)==0); pthread_mutex_unlock(&lock);
    pthread_mutex_lock(&core); ends++;
}
static void *sender(void *unused) {
    (void)unused; pthread_mutex_lock(&core);
    deliver(0x0a0d2502,4000,5000,(const uint8_t*)"actual datagram",15);
    pthread_mutex_unlock(&core); return NULL;
}
int main(void) {
    Service svc={0}; IpcReq req={.cmd=2,.data_size=12}; IpcResp resp={0};
    *(int32_t*)req.data=2; *(int32_t*)(req.data+4)=2; client(&svc,&req,&resp);
    int fd=*(int32_t*)resp.data; assert(fd>=0); socks[fd].port=5000;
    req.cmd=20; *(int32_t*)req.data=fd; *(int32_t*)(req.data+4)=4; *(int32_t*)(req.data+8)=0x800;
    client(&svc,&req,&resp); assert(socks[fd].nonblock);
    *(int32_t*)(req.data+4)=3; client(&svc,&req,&resp); assert(*(int32_t*)resp.data==0x802);
    *(int32_t*)(req.data+4)=4; *(int32_t*)(req.data+8)=0; client(&svc,&req,&resp);
    uint8_t data[32]={0}; req.cmd=9; *(int32_t*)req.data=fd; *(int32_t*)(req.data+4)=0;
    req.out[0]=(IpcBuf){(uintptr_t)data,sizeof data};
    pthread_mutex_lock(&core); pthread_t th; pthread_create(&th,NULL,sender,NULL);
    client(&svc,&req,&resp); pthread_mutex_unlock(&core); pthread_join(th,NULL);
    assert(begins==1 && ends==1 && *(int32_t*)resp.data==15);
    assert(!memcmp(data,"actual datagram",15));
    req.cmd=26; client(&svc,&req,&resp); assert(!socks[fd].used);
    return 0;
}
''',
            }.items():
                source = out/f'{name}_test.c'
                source.write_text(f'#include "runtime/services/{name}.c"\n'+check, encoding='utf-8')
                exe = out/f'{name}.exe'
                subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG',
                                '-O1', str(source), '-o', str(exe), '-lpthread'],
                    env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache')), check=True)
                subprocess.run([str(exe)], check=True, timeout=10)
