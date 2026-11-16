"""Exercise prepared guest-thread creation with Windows reserve/commit semantics."""
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


@unittest.skipUnless(os.name == 'nt' and DEFAULT_COMPILER.is_file(), 'Windows native compiler required')
class ThreadStackTests(unittest.TestCase):
    def test_guest_stack_reserves_space_and_commits_on_demand(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'kernel.c').read_text(encoding='utf-8')
            start = text.index('static void thread_start(KThread *t) {')
            function = text[start:text.index('\n}\n', start)+3]
            source = out/'stack.c'
            source.write_text(r'''
#include <windows.h>
#include <pthread.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef struct { pthread_t host; bool started; uint64_t id, entry; size_t before, after, reserve; } KThread;
#define LOG(...) fprintf(stderr,__VA_ARGS__)
static void threads_reap(void) {}
static size_t stack_committed(size_t *total) {
    volatile char marker;
    MEMORY_BASIC_INFORMATION info;
    assert(VirtualQuery((void*)&marker,&info,sizeof info));
    char *base=info.AllocationBase, *at=base; size_t committed=0;
    while(VirtualQuery(at,&info,sizeof info) && info.AllocationBase==base) {
        if(info.State==MEM_COMMIT) committed+=info.RegionSize;
        at+=info.RegionSize;
    }
    *total=(size_t)(at-base); return committed;
}
__attribute__((noinline)) static unsigned recurse(unsigned depth) {
    volatile unsigned char page[32768];
    for(unsigned i=0;i<sizeof page;i++)page[i]=(unsigned char)depth;
    unsigned result=depth?recurse(depth-1):0;
    for(unsigned i=0;i<sizeof page;i+=4096)result+=page[i];
    return result;
}
static void *thread_main(void *arg) {
    KThread *t=arg;
    t->before=stack_committed(&t->reserve);
    volatile unsigned result=recurse(64);
    (void)result;
    t->after=stack_committed(&t->reserve);
    return NULL;
}
''' + function + r'''
int main(void) {
    KThread threads[8]={0};
    for(unsigned i=0;i<8;i++){thread_start(&threads[i]);assert(threads[i].started);}
    for(unsigned i=0;i<8;i++) {
        assert(!pthread_join(threads[i].host,NULL));
        assert(threads[i].reserve >= (256ull<<20));
        assert(threads[i].before < (1ull<<20));
        fprintf(stderr,"thread %u: reserve=%zu initial=%zu grown=%zu\n",i,threads[i].reserve,threads[i].before,threads[i].after);
        assert(threads[i].after > (2ull<<20) && threads[i].after < (8ull<<20));
    }
    printf("Stack: reserve=%zu, initial commit=%zu, after recursion=%zu bytes\n",
           threads[0].reserve,threads[0].before,threads[0].after);
    return 0;
}
''', encoding='utf-8')
            exe = out/'stack.exe'
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG',
                            '-O1', '-UNDEBUG', str(source), '-o', str(exe), '-lpthread',
                            '-Wl,--stack,268435456'],
                env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache')), check=True)
            subprocess.run([str(exe)], check=True, timeout=20)
