"""Exercise actual prepared alias ownership and pool retirement without a GPU driver."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import prepare_host_sources
from native import DEFAULT_COMPILER, compiler_env
import pool_release


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class PoolReleaseTests(unittest.TestCase):
    def test_shared_orphan_and_nonaliased_images_retire_once_and_recreate(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out/'host')
            text = (runtime/'nvn/vulkan/textures.c').read_text(encoding='utf-8')
            body = text[text.index('static void destroy_image('):text.index('// Reboot (device.c')]
            # Statistics are separate from the ownership code under test.
            a, b = body.index('int mk8_live_textures('), body.index('static void live_remove(')
            body = body[:a] + body[b:]
            range_code = text[text.index('// MK8 (toolchain/pool_release.py): keep shared'):text.index('void vk_textures_reset(')]
            unlisted = text[text.index('// The first owner can be finalized'):]
            fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#define LOG(...) ((void)0)
#define FATAL(...) abort()
#define VK_NULL_HANDLE 0
#define GUEST_LO 0x1000
#define GUEST_HI 0x100000
typedef struct TexBackend {
    uint64_t image, mem, data_hash, sample_hash, checked_frame, full_frame, touched_frame, frame_cleared;
    unsigned wgen, level_gen;
    int refs, reg;
    bool scalable, uploaded, gpu_owned, dynamic, presented;
    float sx, sy;
    void *pending_rgba;
} TexBackend;
typedef struct {
    uint64_t addr; unsigned format, width, height, depth, levels, target, samples;
    char label[64]; TexBackend *backend;
} NvnTexture;
typedef struct { uint64_t img, mem, view; } Dead;
typedef struct { uint64_t size; } VmaAllocationInfo;
static struct { int vma; } g_vk;
static float g_rs_x=1, g_rs_y=1;
static unsigned retired, forgotten, creates, ended;
static Dead deferred[16];
static void defer(Dead d) { deferred[retired++]=d; }
static void views_forget(TexBackend *b) { if (b->image) forgotten++; }  // (textures.c: a scan, also without an image)
static void vk_collect_dead(void) {}
static bool scalable_on;
static bool tex_scalable(NvnTexture *t) { (void)t; return scalable_on; }
static bool mk8_vram_traced(void) { return false; }
static uint64_t frames = 1;
static uint64_t renderer_present_count(void) { return frames; }
static bool tight;
bool mk8_vram_tight(void) { return tight; }
static void create_image(NvnTexture *t, TexBackend *b) { (void)t; b->image=++creates; b->mem=128; }
static void vmaGetAllocationInfo(int vma, uint64_t mem, VmaAllocationInfo *info) { (void)vma; info->size=mem; }
static void vk_end_pass(void) { ended++; }
void vk_alias_texture_destroyed(NvnTexture *t) { (void)t; }
void mk8_pool_copy_aliases(uint64_t lo, uint64_t hi) { (void)lo; (void)hi; }
'''
            # mk8_used is only needed by the optional diagnostic inserted in tex_backend.
            fixture = fixture.replace('int refs, reg;', 'int refs, reg; uint64_t mk8_used, mk8_orphaned, mk8_last_use; bool mk8_orphan_listed;')
            checks = r'''
int main(void) {
    NvnTexture a={.addr=0x1000,.width=16}, b=a, c={.addr=0x1800}, d=c;
    NvnTexture upper={.addr=0x2000}, outside={.addr=0x4000}, nonalias={.addr=0x100};
    TexBackend *shared=tex_backend(&a); assert(tex_backend(&b)==shared && shared->refs==2);
    backend_texture_destroyed(&a);  // first owner gone: the alias table still owns discovery of this backend
    assert(g_nlive==0 && shared->refs==1 && b.backend==shared);
    uint64_t orphan_bytes=0; assert(mk8_unlisted_images(&orphan_bytes)==1 && orphan_bytes==128);
    TexBackend *two=tex_backend(&c); assert(tex_backend(&d)==two && two->refs==2);
    tex_backend(&upper); tex_backend(&outside); tex_backend(&nonalias);
    shared->gpu_owned=shared->uploaded=true; shared->data_hash=123;
    uint64_t bytes=0;
    assert(mk8_textures_release_range(0x1000,0x2000,&bytes)==2 && bytes==256);
    assert(retired==2 && forgotten==2 && ended==2);  // deferred once, no Vulkan immediate free
    assert(!shared->image && !two->image && upper.backend->image && outside.backend->image);
    assert(b.backend==shared && c.backend==two && d.backend==two && two->refs==2);
    assert(!shared->uploaded && !shared->gpu_owned);
    orphan_bytes=0; assert(mk8_unlisted_images(&orphan_bytes)==0 && !orphan_bytes);
    assert(mk8_textures_release_range(0x1000,0x2000,&bytes)==0 && bytes==256);
    assert(tex_backend(&b)==shared && shared->image && !shared->uploaded && !shared->data_hash);
    assert(tex_backend(&d)==two && two->image && tex_backend(&c)==two);
    assert(mk8_textures_release_range(0x2000,0x1000,&bytes)==0);
    assert(mk8_textures_release_range(0x100,0x101,&bytes)==1 && bytes==384);
    assert(!nonalias.backend->image && outside.backend->image);
    backend_texture_destroyed(&b); backend_texture_destroyed(&c); backend_texture_destroyed(&d);
    backend_texture_destroyed(&upper); backend_texture_destroyed(&outside); backend_texture_destroyed(&nonalias);
    assert(!g_nlive && !mk8_norphans);  // finalized: freed at once
    NvnTexture e={.addr=0x1000,.width=16}, f=e, g={.addr=0x1000,.width=32}, h=g;
    TexBackend *kept=tex_backend(&e); unsigned before=retired, kept_image=(unsigned)kept->image;
    mk8_texture_detach(&e);  // a re-initialized object lets go of the image: kept for the next one
    assert(!e.backend && mk8_norphans==1 && retired==before && kept->image);
    assert(tex_backend(&f)==kept && kept->refs==1);  // taken back with its image
    TexBackend *wide=tex_backend(&g); mk8_texture_detach(&f); mk8_texture_detach(&g); mk8_texture_detach(&nonalias);
    assert(mk8_norphans==2);
    frames+=MK8_ORPHAN_FRAMES; assert(tex_backend(&h)==wide && mk8_norphans==2);  // (the 32-wide one taken back)
    frames++; tex_backend(&e);
    assert(!mk8_norphans && retired==before+1 && e.backend->image!=kept_image);  // (the freed backend's memory may be reused)  // an expired shape is freed
    // dynamic resolution: a new target size retires the other sizes over its storage unused for 2 s
    scalable_on=true;
    NvnTexture s1={.addr=0x5000,.width=64,.height=36}, s2={.addr=0x5000,.width=60,.height=34}, s3={.addr=0x5000,.width=56,.height=32};
    TexBackend *b1=tex_backend(&s1), *b2=tex_backend(&s2);
    assert(b1->image && b2->image);  // both just used: kept
    frames+=MK8_ORPHAN_FRAMES+1; tex_backend(&s2);
    before=retired; TexBackend *b3=tex_backend(&s3);
    assert(!b1->image && b2->image && b3->image && retired==before+1);
    assert(tex_backend(&s1)==b1 && b1->image && b1->refs==1);  // a later use recreates it
    // memory pressure: both wait 2 frames instead of 2 s
    tight=true;
    NvnTexture s4={.addr=0x5000,.width=52,.height=30};
    frames+=3; tex_backend(&s3);
    before=retired; TexBackend *b4=tex_backend(&s4);
    assert(!b1->image && !b2->image && b3->image && b4->image && retired==before+2);  // (s1 and s2 unused 3 frames)
    NvnTexture o1={.addr=0x9000,.width=8}, o2=o1, o3={.addr=0x9000,.width=4};
    TexBackend *ob=tex_backend(&o1); mk8_texture_detach(&o1);
    assert(mk8_norphans==1);
    frames+=3; before=retired; tex_backend(&o3);
    assert(!mk8_norphans && retired==before+1 && tex_backend(&o2)!=ob);  // not taken back within 2 frames: freed
    tight=false;
    scalable_on=false;
    return 0;
}
'''
            source = out/'pool.c'
            source.write_text(fixture + body + range_code + unlisted + checks, encoding='utf-8')
            exe = out/'pool.exe'
            result = subprocess.run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                                     source, '-o', exe], capture_output=True, text=True, env=compiler_env())
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([exe], check=True, timeout=30, env=compiler_env())

    def test_deferred_copies_into_other_pools_keep_their_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out/'host')
            text = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            valid = text[text.index('static bool alias_valid('):text.index('// the real copy of the aspects')]
            release = text[text.index('// Resolve deferred depth copies'):text.index('void vk_alias_reset(')]
            source = out/'copies.c'
            source.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
typedef struct { uint64_t image; unsigned aspect; } TexBackend;
typedef struct { uint64_t addr; TexBackend *backend; } NvnTexture;
typedef struct { NvnTexture *src, *dst; uint64_t simg, dimg; } CopyAlias;
static CopyAlias aliases[8]; static int naliases;
static unsigned vk_view_gen, copies;
static void alias_copy(CopyAlias *a, unsigned aspects, const char *why) {
    assert(a->src->backend->image && a->dst->backend->image);
    assert(aspects==a->dst->backend->aspect); (void)why; copies++;
}
''' + valid + release + r'''
int main(void) {
    TexBackend s={1,6}, d={2,6};
    NvnTexture inside={0x1000,&s}, outside={0x2000,&d}, other={0x3000,&s};
    aliases[0]=(CopyAlias){&inside,&outside,1,2}; // preserve the destination outside the pool
    aliases[1]=(CopyAlias){&outside,&inside,2,1}; // resolve destination inside the pool
    aliases[2]=(CopyAlias){&inside,&outside,99,2}; // stale image: discard without copying
    aliases[3]=(CopyAlias){&other,&outside,1,2}; // exclusive upper bound: leave alone
    naliases=4; mk8_pool_copy_aliases(0x1000,0x2000);
    assert(copies==2 && vk_view_gen==3 && naliases==1 && aliases[0].src==&other);
    return 0;
}
''', encoding='utf-8')
            exe = out/'copies.exe'
            result = subprocess.run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                                     source, '-o', exe], capture_output=True, text=True, env=compiler_env())
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([exe], check=True, timeout=30, env=compiler_env())

    def test_patch_rejects_unknown_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn').mkdir()
            (runtime/'nvn/objects.c').write_text('int unrelated;')
            with self.assertRaises(ValueError):
                pool_release.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
