"""A texture first drawn to while its guest memory is zero starts zeroed; memory with data is still uploaded."""
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
import texture_init


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class TextureInitTests(unittest.TestCase):
    def test_first_render_use_follows_guest_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'nvn/vulkan/textures.c').read_text(encoding='utf-8')
            code = text[text.index('// MK8: zeroed guest memory -> zeroed image'):]
            code = code[:code.index('\nTexBackend *vk_tex(NvnTexture *t, bool for_render) {')]
            fixture = r'''
#include <stdlib.h>
#include <string.h>
#include "nvn/nvn.h"
#include "nvn/vulkan/vk.h"
static int clears, uploads, attachable = 1;
static VkClearColorValue cleared;
static VkImageSubresourceRange range;
VkCommandBuffer vk_upload_cmd(void) { return NULL; }
static bool format_supports(VkFormat f, VkFormatFeatureFlags want) { (void)f; return attachable && want == VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT; }
static void upload_texture(NvnTexture *t, TexBackend *b) { (void)t; (void)b; uploads++; }
bool renderer_format_is_depth(uint32_t f) { return f == 0x36; }
uint64_t tegra_total_size(const TexLayout *l) { (void)l; return 512 * 8; }  /* the test's guest memory */
void VKAPI_CALL vkCmdClearColorImage(VkCommandBuffer cb, VkImage image, VkImageLayout layout, const VkClearColorValue *v,
                                     uint32_t n, const VkImageSubresourceRange *r) {
    (void)cb; (void)image; (void)n; clears += layout == VK_IMAGE_LAYOUT_GENERAL; cleared = *v; range = *r;
}
static void VKAPI_CALL barrier(VkCommandBuffer cb, const VkDependencyInfo *d) { (void)cb; (void)d; }
PFN_vkCmdPipelineBarrier2 vkl_CmdPipelineBarrier2 = barrier;  /* (vk.h: late-bound Vulkan 1.3 command) */
'''
            checks = r'''
int main(void) {
    static uint64_t memory[512];
    static FormatInfo fmt = {0};
    fmt.bpb = 4; fmt.bw = fmt.bh = 1;
    NvnTexture t; memset(&t, 0, sizeof t);
    t.addr = (uint64_t)(uintptr_t)memory; t.target = 8; t.width = t.height = 32; t.depth = 1; t.layout.fmt = &fmt;
    TexBackend b; memset(&b, 0, sizeof b);
    b.image = (VkImage)(uintptr_t)1; b.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
    first_render_use(&t, &b);  /* zeroed memory: cleared, every level and layer */
    VkClearColorValue zero; memset(&zero, 0, sizeof zero);
    if (clears != 1 || uploads || memcmp(&cleared, &zero, sizeof zero) || range.levelCount != VK_REMAINING_MIP_LEVELS ||
        range.layerCount != VK_REMAINING_ARRAY_LAYERS) return 1;
    memory[3] = 0x3c00;  /* memory with data: uploaded, not cleared */
    first_render_use(&t, &b);
    if (clears != 1 || uploads != 1) return 2;
    memory[3] = 0; b.scalable = true;  /* screen-sized scaled target: untouched */
    first_render_use(&t, &b);
    b.scalable = false; attachable = 0;  /* not a colour attachment format (compressed): untouched */
    first_render_use(&t, &b);
    if (clears != 1 || uploads != 1) return 3;
    attachable = 1; b.scalable = true; b.format = VK_FORMAT_B10G11R11_UFLOAT_PACK32;
    memory[3] = 0x3f7fffff;
    first_render_use(&t, &b);
    if (clears != 2 || uploads != 1) return 4;
    b.uploaded = true;
    first_render_use(&t, &b);
    b.uploaded = false; b.gpu_owned = true;
    first_render_use(&t, &b);
    if (clears != 2 || uploads != 1) return 5;
    b.gpu_owned = false; b.scalable = false; b.levels = 2;
    first_render_use(&t, &b);  /* Rendered reflection cube starts with all faces and mips defined. */
    if (clears != 3 || uploads != 1 || range.levelCount != VK_REMAINING_MIP_LEVELS ||
        range.layerCount != VK_REMAINING_ARRAY_LAYERS) return 6;
    t.width = t.height = 64;
    first_render_use(&t, &b);
    return clears == 3 && uploads == 2 ? 0 : 7;
}
'''
            source = out/'init.c'
            source.write_text(fixture + code + checks, encoding='utf-8')
            exe = out/'init.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1', '-I'+str(runtime),
                                    '-I'+str(DEPS/'include'), str(source), '-o', str(exe)], env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'nvn/vulkan').mkdir(parents=True)
            (runtime/'nvn/vulkan/textures.c').write_text(texture_init.BOUNDARY + '}\n')
            with self.assertRaises(ValueError):
                texture_init.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
