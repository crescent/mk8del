import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from native import DEFAULT_COMPILER
from host import prepare_host_sources


class TextureLifetimeTests(unittest.TestCase):
    def test_preparation_covers_recording_and_replay_owners(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', Path(folder))
            text = (runtime/'nvn/cmdbuf.c').read_text()
            self.assertIn('mk8_command_texture(r, rt.depth)', text)
            self.assertIn('mk8_command_texture(r, ct.tex)', text)
            self.assertEqual(text.count('mk8_command_texture(r, t.tex)'), 2)
            self.assertEqual(text.count('mk8_command_texture(r, t[1].tex)'), 3)
            self.assertIn('cb->buf_used = 0;  // references now belong', text)
            self.assertEqual(text.count('mk8_free_list(graveyard['), 2)
            self.assertIn('mk8_copy_command_textures(d, r)', text)
            self.assertIn('mk8_rt_release(&prev)', (runtime/'nvn/vulkan/exec.c').read_text())
            objects = (runtime/'nvn/objects.c').read_text()
            start = objects.index('NVN(nvnTextureInitialize)')
            adopt = objects.index('mk8_texture_adopt(A(0), t)')
            self.assertLess(start, adopt)
            self.assertLess(adopt, objects.index('SET_HOST(A(0), t);', start))
            self.assertLess(objects.index('NVN(nvnTextureFinalize)'), objects.index('if (t) mk8_texture_disown(t);'))
            textures = (runtime/'nvn/vulkan/textures.c').read_text()
            self.assertIn('    if (mk8_orphan_keep(t, b)) return;', textures)
            self.assertIn('void mk8_texture_detach(NvnTexture *t) {', textures)
            self.assertLess(textures.index('mk8_orphans_sweep();'), textures.index('a->b->refs++;'))

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_finalize_before_replay_and_recording_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = root/'ownership.c'
            code.write_text('''#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define FATAL(...) abort()
typedef struct { unsigned mk8_refs, mk8_command_refs, mk8_finalized; unsigned marker; uint64_t mk8_owner; } NvnTexture;
static unsigned destroyed;
static void backend_texture_destroyed(NvnTexture *t) { assert(t->marker==42); destroyed++; }
static unsigned detached;
void mk8_texture_detach(NvnTexture *t) { assert(t->marker==42 && t->mk8_refs>=2); detached++; }
#include "graphics/texture_refs.inc"
typedef struct { uint32_t op,size; struct Mk8TextureRefs *mk8_textures; } CmdRec;
#define CMD_END 0
#include "graphics/command_texture_refs.inc"
int main(void) {
 NvnTexture *t=calloc(1,sizeof *t); t->mk8_refs=1;t->marker=42;
 CmdRec buffer[2]={{1,sizeof(CmdRec),NULL},{0,sizeof(CmdRec),NULL}};
 mk8_command_texture(buffer,(uintptr_t)t);mk8_command_texture(buffer,(uintptr_t)t);
 assert(t->mk8_refs==2); // duplicate color/depth ownership is deduplicated per command
 assert(t->mk8_command_refs==(getenv("MK8_TRACE_VRAM") ? 1u : 0u));
 CmdRec *list=malloc(sizeof buffer);memcpy(list,buffer,sizeof buffer);
 size_t used=0; // EndRecording transferred ownership; resetting/finalizing the builder cannot drop it
 mk8_release_records((uint8_t*)buffer,used);
 mk8_texture_release(t); // game finalizes before queued execution
 assert(!destroyed && t->marker==42 && t->mk8_refs==1);
 CmdRec copy[2];memcpy(copy,list,sizeof copy);mk8_copy_command_textures(copy,list);
 assert(t->mk8_refs==2 && copy[0].mk8_textures!=list[0].mk8_textures);
 mk8_texture_retain(t); // executor state keeps the target across lists
 mk8_release_records((uint8_t*)copy,sizeof copy);
 mk8_free_list((uint8_t*)list);assert(!destroyed && t->mk8_refs==1);
 assert(t->mk8_command_refs==0);
 mk8_texture_release(t);assert(destroyed==1);
 t=calloc(1,sizeof *t);t->mk8_refs=1;t->marker=42;
 memset(buffer,0,sizeof buffer);buffer[0].op=1;buffer[0].size=sizeof(CmdRec);
 mk8_command_texture(buffer,(uintptr_t)t);
 mk8_texture_release(t);
 mk8_release_records((uint8_t*)buffer,sizeof(CmdRec)); // abandoned recording
 assert(destroyed==2 && !buffer[0].mk8_textures);
 mk8_texture_release(NULL);mk8_command_texture(buffer,0);
 // the same guest object initialized again without finalize: the earlier texture comes back to be released once
 NvnTexture *a=calloc(1,sizeof *a),*b=calloc(1,sizeof *b),*c=calloc(1,sizeof *c);
 a->mk8_refs=b->mk8_refs=c->mk8_refs=1;a->marker=b->marker=c->marker=42;
 assert(!mk8_texture_adopt(0x1000,a) && !mk8_texture_adopt(0x1000+4096*16,c)); // (same bucket)
 assert(mk8_texture_adopt(0x1000,b)==a);
 mk8_texture_abandoned(a);  // its image is let go; the texture stays for copies of the object
 assert(detached==1 && a->mk8_refs==1 && destroyed==2);
 mk8_texture_disown(a);  // a copy finalized later never removes the current entry
 mk8_texture_release(a);assert(destroyed==3);
 mk8_texture_disown(b);assert(!mk8_texture_adopt(0x1000,b)); // finalized: nothing left to release
 mk8_texture_disown(c);assert(!mk8_texture_adopt(0x1000+4096*16,c));
 return 0;
}
''', encoding='utf-8')
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', root/'prepared')
            commands = (runtime/'nvn/cmdbuf.c').read_text(encoding='utf-8')
            refs = commands[commands.index('struct Mk8TextureRefs {'):commands.index('static void mk8_retire_command_buffer')]
            code.write_text(code.read_text(encoding='utf-8').replace('#include "graphics/command_texture_refs.inc"', refs),
                            encoding='utf-8')
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            exe = root/'ownership.exe'
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'runtime/mk8'), str(code), '-o', str(exe)], env=env, check=True)
            env.pop('MK8_TRACE_VRAM', None)
            subprocess.run([str(exe)], check=True, env=env)
            subprocess.run([str(exe)], check=True, env=dict(env, MK8_TRACE_VRAM='1'))

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_finalized_buffers_and_pools_retire_lists_without_early_release(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out/'prepared')
            text = (runtime/'nvn/cmdbuf.c').read_text(encoding='utf-8')
            refs = text[text.index('struct Mk8TextureRefs {'):text.index('static void mk8_retire_command_buffer')]
            lists = text[text.index('typedef struct ListInfo {'):text.index('static uint64_t est_used(')]
            finalize = text[text.index('NVN(nvnCommandBufferFinalize)'):text.index('NVN(nvnCommandBufferSetDebugLabel)')]
            source = out/'retire.c'
            source.write_text(r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define FATAL(...) abort()
#define LOG(...) ((void)0)
#define GUEST_LO 0x1000
#define GUEST_HI 0x100000
typedef struct { unsigned mk8_refs, mk8_command_refs; uint64_t mk8_owner; } NvnTexture;
static unsigned destroyed;
static void backend_texture_destroyed(NvnTexture *t) { (void)t; destroyed++; }
void mk8_texture_detach(NvnTexture *t) { (void)t; }
#include "graphics/texture_refs.inc"
typedef struct { uint32_t op,size; struct Mk8TextureRefs *mk8_textures; } CmdRec;
#define CMD_END 0
typedef struct { uint8_t *buf, *calls_only[64]; uint64_t buf_used; } NvnCmdBuf;
typedef struct { uint64_t x[1]; } Cpu;
#define NVN(name) void name(Cpu *c)
#define A(i) (c->x[i])
#define SET_HOST(addr, ptr) (*(NvnCmdBuf **)(uintptr_t)(addr) = (ptr))
static NvnCmdBuf *cb_of(uint64_t addr) { return *(NvnCmdBuf **)(uintptr_t)addr; }
''' + refs + lists + finalize + r'''
static uint8_t *record(NvnTexture *t) {
    CmdRec *r = calloc(2, sizeof *r);
    r[0].op = 1; r[0].size = sizeof *r; r[1].size = sizeof *r;
    mk8_command_texture(r, (uintptr_t)t);
    return (uint8_t *)r;
}
static void active(uintptr_t pool, uint64_t start, uint64_t end, uint8_t *mem) {
    ListInfo *l = calloc(1, sizeof *l);
    unsigned b = bucket(start >> PAGE_SHIFT_L);
    *l = (ListInfo){pool,start,end,mem,list_buckets[b]}; list_buckets[b] = l;
}
int main(int argc, char **argv) {
    (void)argv;
    if (argc > 1) { mk8_commands_pool_finalized(1, UINT64_MAX, 2); return 99; }
    NvnTexture *t = calloc(1, sizeof *t); t->mk8_refs = 1;
    NvnCmdBuf *cb = calloc(1, sizeof *cb);
    cb->calls_only[0] = record(t); cb->calls_only[63] = record(t);
    cb->buf = record(t); cb->buf_used = 2 * sizeof(CmdRec);
    active(1, 0x2000, 0x2100, record(t));
    active(1, 0x2800, 0x3000, record(t));
    active(3, 0x3000, 0x3100, record(t));  // exclusive upper bound survives
    active(2, 0x2000, 0x2100, record(t));  // another pool aliases the same storage
    mk8_texture_release(t);
    Cpu c = {{(uint64_t)(uintptr_t)&cb}};
    nvnCommandBufferFinalize(&c);
    assert(!cb && !destroyed && grave_head == 2 && t->mk8_refs == 6);
    nvnCommandBufferFinalize(&c); // repeated finalize: no references dropped twice
    mk8_commands_pool_finalized(1, 0x2000, 0x1000);
    assert(grave_head == 4 && !destroyed && t->mk8_refs == 6);
    mk8_commands_pool_finalized(1, 0x2000, 0x1000);
    assert(grave_head == 4);
    list_clock = GRAVE_DELAY;
    retire_overlapping(0x80000, 0x80100);
    assert(grave_tail == 0 && t->mk8_refs == 6); // existing delay remains intact
    list_clock++;
    retire_overlapping(0x80000, 0x80100);
    assert(grave_tail == 4 && t->mk8_refs == 2 && !destroyed);
    // A new list at the same address after finalization must remain alive.
    active(1, 0x2000, 0x2100, record(t));
    mk8_commands_pool_finalized(3, 0x3000, 0x100);
    list_clock += GRAVE_DELAY + 1;
    retire_overlapping(0x80000, 0x80100);
    assert(t->mk8_refs == 2 && !destroyed);
    mk8_commands_pool_finalized(1, 0x2000, 0x100);
    list_clock += GRAVE_DELAY + 1;
    retire_overlapping(0x80000, 0x80100);
    assert(t->mk8_refs == 1 && !destroyed); // storage alias remains valid
    mk8_commands_pool_finalized(2, 0x2000, 0x100);
    list_clock += GRAVE_DELAY + 1;
    retire_overlapping(0x80000, 0x80100);
    assert(destroyed == 1 && grave_head == grave_tail);
    return 0;
}
''', encoding='utf-8')
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            exe = out/'retire.exe'
            result = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                                     '-I'+str(ROOT/'runtime/mk8'), str(source), '-o', str(exe)],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(exe)], check=True, timeout=30, env=dict(env, MK8_TRACE_VRAM='1'))
            result = subprocess.run([str(exe), 'invalid'], timeout=30, env=env, capture_output=True)
            self.assertNotIn(result.returncode, (0, 99))
