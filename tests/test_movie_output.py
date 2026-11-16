"""Menu videos: a decoded frame goes into the buffer the game hands the decoder, not only into the decoder's own."""
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
import movie_output


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class MovieOutputTests(unittest.TestCase):
    def test_frame_lands_in_the_game_buffer(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out)
            text = (runtime/'hle/movie.c').read_text(encoding='utf-8')
            start = text.index('static void dec_get_output_buffer(Cpu *c) {')
            end = text.index('\n}\n', start) + 3
            source = out/'movie.c'
            source.write_text(r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LOG(...) ((void)0)
#define NOUT 2
typedef struct { uint64_t x[8]; } Cpu;
typedef struct { uint64_t base, capacity; int32_t offset, size; int64_t i64; uint32_t u32; int32_t i32; } MBuffer;
#define BUF(g) ((MBuffer *)(uintptr_t)(g))
typedef struct NvnTexture NvnTexture;
enum { AV_PIX_FMT_NV12, AV_PIX_FMT_RGBA, SWS_BILINEAR };
static void *sws_getContext(int a, int b, int c, int d, int e, int f, int g, void *h, void *i, void *j) {
    (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; (void)g; (void)h; (void)i; (void)j; return (void *)1; }
static int sws_scale(void *s, const uint8_t *const *a, const int *b, int c, int d, uint8_t *const *e, const int *f) {
    (void)s; (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; return 0; }
static int uploads;
static void renderer_upload_rgba(NvnTexture *t, const void *p, uint32_t w, uint32_t h, uint32_t s) {
    (void)t; (void)p; (void)w; (void)h; (void)s; uploads++; }
typedef struct {
    bool video;
    int width, height, stride;
    uint8_t *out_mem[NOUT];
    uint32_t out_cap, out_size[NOUT];
    void *sws;
    uint8_t *rgba;
} Decoder;
static Decoder dec;
static void *self_state(Cpu *c) { (void)c; return &dec; }
static uint64_t n_get_out;
''' + text[start:end] + r'''
int main(void) {
    static uint8_t ours[2][96], game[96];
    dec.video = true; dec.width = 8; dec.height = 8; dec.stride = 8; dec.out_cap = 96;
    dec.out_mem[0] = ours[0]; dec.out_mem[1] = ours[1]; dec.out_size[1] = 96;
    for (int k = 0; k < 96; k++) ours[1][k] = (uint8_t)(k + 1);
    if (getenv("MK8_MOVIE_OWN_BUFFER")) {  /* upstream: the decoder's buffer even when the game has one */
        MBuffer g2 = {.base = (uint64_t)(uintptr_t)game, .capacity = 96};
        Cpu c5 = {{0, 1, (uint64_t)(uintptr_t)&g2}};
        dec_get_output_buffer(&c5);
        return g2.base == (uint64_t)(uintptr_t)ours[1] && game[0] == 0 ? 0 : 5;
    }
    MBuffer b = {.base = (uint64_t)(uintptr_t)game, .capacity = 96};
    Cpu c = {{0, 1, (uint64_t)(uintptr_t)&b}};
    dec_get_output_buffer(&c);
    /* the game's buffer receives the frame and stays the Buffer's base */
    if (c.x[0] || b.base != (uint64_t)(uintptr_t)game || b.size != 96 || memcmp(game, ours[1], 96)) return 1;
    /* without a buffer of its own (or one too small) the game gets the decoder's */
    MBuffer none = {0};
    Cpu c2 = {{0, 1, (uint64_t)(uintptr_t)&none}};
    dec_get_output_buffer(&c2);
    if (none.base != (uint64_t)(uintptr_t)ours[1] || none.size != 96) return 2;
    static uint8_t small[16];
    MBuffer tiny = {.base = (uint64_t)(uintptr_t)small, .capacity = 16};
    Cpu c3 = {{0, 1, (uint64_t)(uintptr_t)&tiny}};
    dec_get_output_buffer(&c3);
    if (tiny.base != (uint64_t)(uintptr_t)ours[1]) return 3;
    /* texture mode (capacity 8: a pointer to the game's texture) still uploads RGBA */
    NvnTexture *tex = (NvnTexture *)(uintptr_t)0x10;
    MBuffer t = {.base = (uint64_t)(uintptr_t)&tex, .capacity = 8};
    Cpu c4 = {{0, 1, (uint64_t)(uintptr_t)&t}};
    dec_get_output_buffer(&c4);
    return uploads == 1 ? 0 : 4;
}
''', encoding='utf-8')
            exe = out/'movie.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1', str(source), '-o', str(exe)],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            plain = {k: v for k, v in os.environ.items() if k != 'MK8_MOVIE_OWN_BUFFER'}
            subprocess.run([str(exe)], check=True, timeout=30, env=plain)
            subprocess.run([str(exe)], check=True, timeout=30, env=dict(plain, MK8_MOVIE_OWN_BUFFER='1'))

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'hle').mkdir(parents=True)
            (runtime/'hle/movie.c').write_text('int x;\n')
            with self.assertRaises(ValueError):
                movie_output.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
