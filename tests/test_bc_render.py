"""Colour targets over block-compressed images: drawn into a shadow image of their blocks, then copied into the level."""
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
import bc_render


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class BcRenderTests(unittest.TestCase):
    def test_block_views_draw_into_shadows_and_copy_back(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            execute = (runtime/'nvn/vulkan/exec.c').read_text(encoding='utf-8')
            for _, new in (bc_render.TARGET, bc_render.EXTENT, bc_render.END_PASS):
                self.assertIn(new, execute)
            start = execute.index('// MK8 (toolchain/bc_render.py): colour targets')
            helpers = execute[start:execute.index('static void vk_end_pass_at(int line) {', start)]
            source = out/'bc.c'
            source.write_text(r'''
#include <stdlib.h>
#include <string.h>
#include "nvn/nvn.h"
#include "nvn/vulkan/vk.h"
#define LOG(...) ((void)0)
VkG g_vk;
static struct { struct { bool has_cview[8]; NvnTextureView cview[8]; } rt; } st;
static int images, views, copies, barriers, layouts;
static VkImageCopy last;
static VkImage last_dst, last_src;
VkFormat vk_format(uint32_t f) { return (VkFormat)f; }  /* (the test passes Vulkan formats as the view's) */
void vk_full_barrier(VkCommandBuffer cb) { (void)cb; barriers++; }
void vk_image_init_layout(VkCommandBuffer cb, VkImage img, VkImageAspectFlags aspect) { (void)cb; (void)img; (void)aspect; layouts++; }
VkResult vmaCreateImage(VmaAllocator a, const VkImageCreateInfo *ci, const VmaAllocationCreateInfo *ai, VkImage *img,
                        VmaAllocation *mem, VmaAllocationInfo *info) {
    (void)a; (void)ai; (void)mem; (void)info;
    if (!(ci->usage & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT) || ci->format != VK_FORMAT_R32G32B32A32_UINT) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    *img = (VkImage)(uintptr_t)(0x1000 + ++images);
    return VK_SUCCESS;
}
void vmaDestroyImage(VmaAllocator a, VkImage img, VmaAllocation mem) { (void)a; (void)img; (void)mem; }
VKAPI_ATTR VkResult VKAPI_CALL vkCreateImageView(VkDevice d, const VkImageViewCreateInfo *vi, const VkAllocationCallbacks *a, VkImageView *v) {
    (void)d; (void)a;
    *v = (VkImageView)(uintptr_t)(0x2000 + (uintptr_t)vi->image);
    views++;
    return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL vkCmdCopyImage(VkCommandBuffer cb, VkImage src, VkImageLayout sl, VkImage dst, VkImageLayout dl, uint32_t n,
                                          const VkImageCopy *r) {
    (void)cb; (void)sl; (void)dl; (void)n;
    copies++; last = *r; last_src = src; last_dst = dst;
}
''' + helpers + r'''
int main(void) {
    VkImageView original = (VkImageView)(uintptr_t)0x77;
    TexBackend bc3; memset(&bc3, 0, sizeof bc3);
    bc3.image = (VkImage)(uintptr_t)0x55; bc3.format = VK_FORMAT_BC3_UNORM_BLOCK; bc3.width = bc3.height = 128; bc3.levels = 8; bc3.layers = 30;
    uint32_t w = 0, h = 0;
    if (getenv("MK8_BC_RENDER_DIRECT")) {  /* upstream's view, untouched */
        st.rt.has_cview[0] = true; st.rt.cview[0].setmask = 0x7; st.rt.cview[0].format = VK_FORMAT_R32G32B32A32_UINT;
        return mk8_bc_target(NULL, 0, &bc3, original, &w, &h) == original && !w && !mk8_bc_ncopies ? 0 : 20;
    }
    /* level 0, layer 4 through a 128-bit view: a 32x32 shadow (one texel per block), copied into that level and layer */
    st.rt.has_cview[0] = true; st.rt.cview[0].setmask = 0x7; st.rt.cview[0].format = VK_FORMAT_R32G32B32A32_UINT;
    st.rt.cview[0].base_level = 0; st.rt.cview[0].min_layer = 4;
    VkImageView v = mk8_bc_target(NULL, 0, &bc3, original, &w, &h);
    if (v == original || w != 32 || h != 32 || images != 1 || layouts != 1 || mk8_bc_ncopies != 1) return 1;
    mk8_bc_copy_back(NULL);
    if (copies != 1 || last_dst != bc3.image || last.dstSubresource.mipLevel != 0 || last.dstSubresource.baseArrayLayer != 4 ||
        last.extent.width != 32 || last.extent.height != 32 || last.extent.depth != 1 || mk8_bc_ncopies || barriers != 2) return 2;
    /* the same block grid again: the shadow is reused */
    VkImage first_shadow = last_src;
    st.rt.cview[0].min_layer = 5;
    if (mk8_bc_target(NULL, 0, &bc3, original, &w, &h) != v || images != 1) return 3;
    mk8_bc_copy_back(NULL);
    if (last_src != first_shadow || last.dstSubresource.baseArrayLayer != 5) return 4;
    /* level 3 (16x16 texels): 4x4 blocks; level 7 (1x1 texel): one block */
    st.rt.cview[0].base_level = 3;
    mk8_bc_target(NULL, 0, &bc3, original, &w, &h);
    if (w != 4 || h != 4) return 5;
    st.rt.cview[0].base_level = 7;
    mk8_bc_target(NULL, 0, &bc3, original, &w, &h);
    if (w != 1 || h != 1 || images != 3) return 6;
    mk8_bc_copy_back(NULL);
    if (last.dstSubresource.mipLevel != 7 || last.extent.width != 1) return 7;
    /* left as before: no format override, an uncompressed image, a view of another block size (BC1 is 64-bit) */
    int before = copies;
    w = h = 0;
    st.rt.cview[0].setmask = 0x3;
    if (mk8_bc_target(NULL, 0, &bc3, original, &w, &h) != original) return 8;
    st.rt.cview[0].setmask = 0x7;
    TexBackend plain = bc3; plain.format = VK_FORMAT_R16G16B16A16_SFLOAT;
    if (mk8_bc_target(NULL, 0, &plain, original, &w, &h) != original) return 9;
    TexBackend bc1 = bc3; bc1.format = VK_FORMAT_BC1_RGBA_UNORM_BLOCK;
    if (mk8_bc_target(NULL, 0, &bc1, original, &w, &h) != original) return 10;
    mk8_bc_copy_back(NULL);
    return w == 0 && h == 0 && copies == before ? 0 : 11;
}
''', encoding='utf-8')
            exe = out/'bc.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1', '-I'+str(runtime),
                                    '-I'+str(DEPS/'include'), str(source), '-o', str(exe)], env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            plain = {k: v for k, v in os.environ.items() if k != 'MK8_BC_RENDER_DIRECT'}
            subprocess.run([str(exe)], check=True, timeout=30, env=plain)
            subprocess.run([str(exe)], check=True, timeout=30, env=dict(plain, MK8_BC_RENDER_DIRECT='1'))

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/vulkan/exec.c').write_text('static void vk_end_pass_at(int line) {\n}\n')
            with self.assertRaises(ValueError):
                bc_render.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
