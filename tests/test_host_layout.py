"""The IPC host buffer must permit 64-bit clock fields without changing wire offsets."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import prepare_host_sources
from native import DEFAULT_COMPILER


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class HostLayoutTests(unittest.TestCase):
    def test_windows_thread_name_notification_is_continuable(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            windows = (runtime/'platform/windows.c').read_text(encoding='utf-8')
            handler = windows[windows.index('static LONG CALLBACK vectored_handler('):windows.index('// The window keeps write tracking')]
            program = output/'thread-name.c'
            program.write_text('''#include <windows.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
static bool g_report_crashes;
static bool lazy_commit(uint64_t address) { (void)address; return false; }
static bool rt_fault_watch(uint64_t address, uint64_t pc) { (void)address; (void)pc; return false; }
''' + handler + '''
int main(void) {
    void *token = AddVectoredExceptionHandler(1, vectored_handler);
    assert(token);
    const ULONG_PTR args[] = {0x1000, (ULONG_PTR)"test thread", (ULONG_PTR)-1};
    RaiseException(0x406D1388, 0, 3, args);
    EXCEPTION_RECORD fault = {.ExceptionCode=EXCEPTION_ACCESS_VIOLATION, .NumberParameters=2};
    EXCEPTION_POINTERS pointers = {.ExceptionRecord=&fault};
    assert(vectored_handler(&pointers)==EXCEPTION_CONTINUE_SEARCH);
    fault.ExceptionCode=0x406D1388;
    fault.ExceptionInformation[0]=0x1000;
    fault.ExceptionFlags=EXCEPTION_NONCONTINUABLE;
    assert(vectored_handler(&pointers)==EXCEPTION_CONTINUE_SEARCH);
    assert(RemoveVectoredExceptionHandler(token));
}
''', encoding='utf-8')
            exe = output/'thread-name.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG',
                            str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=20)

    def test_settings_values_end_before_a_carriage_return(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            settings = (runtime/'settings.c').read_text(encoding='utf-8')
            line_format = settings[settings.index('sscanf(line, "') + len('sscanf(line, '):settings.index(', key, val)')]
            program = output/'settings-line.c'
            program.write_text('''#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    const char *lines[] = {"resolution = 75%\\r\\n", "antialias = off\\n", "rando_seed = \\r\\n"};
    const char *keys[] = {"resolution", "antialias", "rando_seed"}, *vals[] = {"75%", "off", ""};
    for (int i = 0; i < 3; i++) {
        char key[64] = "", val[128] = "";
        int n = sscanf(lines[i], ''' + line_format + ''', key, val);
        assert(n >= 1 && !strcmp(key, keys[i]) && !strcmp(val, vals[i]));
    }
}
''', encoding='utf-8')
            exe = output/'settings-line.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG',
                            str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=20)

    def test_network_settings_are_kept_between_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output/'mk8net.ini').write_bytes(b'# kept\r\nnet_log = 1\r\nbackend=ours\r\nbackend=local-nex')
            program = output/'net.c'
            program.write_text('''#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
bool game_net_config(const char *key, char *out, size_t cap);
void game_net_config_set(const char *key, const char *val);
int main(void) {
    char v[64] = "";
    assert(game_net_config("backend", v, sizeof v) && !strcmp(v, "local-nex"));
    game_net_config_set("backend", "nextendo");
    assert(game_net_config("backend", v, sizeof v) && !strcmp(v, "nextendo"));
    assert(game_net_config("net_log", v, sizeof v) && !strcmp(v, "1"));
    game_net_config_set("bogus", "1");
    game_net_config_set("net_hosts", "a=1.2.3.4\\nbackend=ours");
    assert(!game_net_config("bogus", v, sizeof v) && !game_net_config("net_hosts", v, sizeof v));
}
''', encoding='utf-8')
            exe = output/'net.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG',
                            str(program), str(ROOT/'runtime/mk8/services/net_config.c'), '-o', str(exe)],
                           env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=20, cwd=output)
            self.assertEqual((output/'mk8net.ini').read_bytes(), b'# kept\nnet_log = 1\nbackend=nextendo\n')

    def test_map_memory_backs_destination_before_copying(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            kernel = (runtime/'kernel.c').read_text(encoding='utf-8')
            case = kernel[kernel.index('    case 0x04:  // MapMemory'):kernel.index('    case 0x05:  // UnmapMemory')]
            program = output/'map.c'
            program.write_text('''#include <windows.h>
#include <assert.h>
#include <stdint.h>
#include <string.h>
static uint64_t GUEST_LO, GUEST_HI;
#define R_INVALID_ADDRESS 0xCC01
#define R_OK 0
static void plat_commit(uint64_t addr, uint64_t size) {
    assert(VirtualAlloc((void *)addr, size, MEM_COMMIT, PAGE_READWRITE));
}
static void region_add(uint64_t addr, uint64_t size, unsigned type, unsigned perm) {
    (void)addr; (void)size; (void)type; (void)perm;
}
static void map(uint64_t x[3], unsigned op) { switch (op) {
''' + case + '''} }
int main(void) {
    unsigned char *space = VirtualAlloc(NULL, 0x40000, MEM_RESERVE, PAGE_READWRITE);
    assert(space);
    GUEST_LO = (uint64_t)space;
    GUEST_HI = GUEST_LO + 0x40000;
    unsigned char *src = space + 0x24000, *dst = space + 0x9000;
    plat_commit((uint64_t)src, 0x19000);
    memset(src, 0xa5, 0x19000);
    uint64_t valid[] = {(uint64_t)dst, (uint64_t)src, 0x19000};
    map(valid, 4);
    assert(valid[0] == R_OK && !memcmp(dst, src, 0x19000));
    uint64_t invalid[] = {GUEST_HI - 0x1000, (uint64_t)src, 0x2000};
    map(invalid, 4);
    assert(invalid[0] == R_INVALID_ADDRESS);
    assert(VirtualFree(space, 0, MEM_RELEASE));
}
''', encoding='utf-8')
            exe = output/'map.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG',
                            str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True, timeout=20)

    def test_movie_path_overload_forwards_to_locked_extractor(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            program = output/'movie.c'
            program.write_text('''#include <stdint.h>
#include <assert.h>
typedef struct { uint64_t x[32]; } Cpu;
static uint64_t expected_path;
static int calls;
static void ex_set_data_source_l(Cpu *c) {
 assert(c->x[0]==123 && c->x[2]==expected_path);calls++;c->x[0]=9;
}
#include "services/movie_file_source.inc"
int main(void) {
 Cpu c={0};const char *path="rom:/Movie/test.webm";
 expected_path=(uintptr_t)path;c.x[0]=123;c.x[1]=expected_path;c.x[2]=456;c.x[19]=789;
 mk8_ex_set_file_source(&c);
 assert(calls==1 && c.x[0]==9 && c.x[19]==789);return 0;
}
''', encoding='utf-8')
            exe = output/'movie.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'runtime/mk8'), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_plural_touch_returns_one_sample_with_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            program = output/'touch.c'
            program.write_text('''#include <stdint.h>
#include <string.h>
typedef struct { uint64_t x[32]; } Cpu;
static unsigned char memory[1400];
#define GUEST_LO ((uintptr_t)memory)
#define GUEST_HI ((uintptr_t)(memory+sizeof memory))
#define HLE_FUNC(symbol,name) void name(Cpu *c)
static void fill_touch(Cpu *c,int n){memset((void *)(uintptr_t)c->x[0],0,16+40*n);}
#include "services/touch_states.inc"
int main(void){
 int capacities[]={-1,0,1,16};
 for(unsigned i=0;i<4;i++){
  memset(memory,0xcc,sizeof memory);Cpu c={0};c.x[0]=GUEST_LO+8;c.x[1]=(uint32_t)capacities[i];
  mk8_touch_states16(&c);int valid=capacities[i]>0;
  if(c.x[0]!=(unsigned)valid || memory[8]!=(valid?0:0xcc) || memory[7]!=0xcc || memory[664]!=0xcc)return 1;
 }
 Cpu c={0};c.x[0]=GUEST_HI-8;c.x[1]=1;mk8_touch_states16(&c);return c.x[0]!=0;
}
''', encoding='utf-8')
            exe = output/'touch.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'runtime/mk8'), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_shared_font_abi_capacity_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            program = output/'fonts.c'
            program.write_text('''#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef struct { uint64_t x[32]; } Cpu;
typedef struct { uint64_t addr, size; } Font;
static unsigned char memory[256];
#define GUEST_LO ((uintptr_t)memory)
#define GUEST_HI ((uintptr_t)(memory+sizeof memory))
#define NFONTS 6
#define LOG(...) ((void)0)
#define HLE_FUNC(symbol,name) void name(Cpu *c)
static Font f={0x12345678,0x1000};
static const Font *font_for(unsigned i) { (void)i; return &f; }
#include "services/shared_font.inc"
int main(void) {
 for (int capacity=0;capacity<=8;capacity++) {
  memset(memory,0xcc,sizeof memory); Cpu c={0};
  c.x[0]=GUEST_LO; c.x[1]=GUEST_LO+9; c.x[2]=capacity;
  mk8_shared_fonts(&c); int32_t n;memcpy(&n,memory,4);
  if(c.x[0]!=1 || n!=6) return 1;
  unsigned count=capacity<6?capacity:6;
  for(unsigned i=0;i<count;i++) {
   uint64_t row[3];memcpy(row,memory+9+i*24,24);
   if(row[0]!=i || row[1]!=f.addr || row[2]!=f.size)return 2;
  }
  if(memory[8]!=0xcc || memory[9+count*24]!=0xcc)return 3;
 }
 Cpu c={0}; c.x[0]=GUEST_LO;c.x[1]=GUEST_HI-8;c.x[2]=6;
 memset(memory,0xcc,sizeof memory);mk8_shared_fonts(&c);
 if(c.x[0]!=0 || memory[0]!=0xcc)return 4;
 c.x[0]=GUEST_LO;c.x[1]=GUEST_LO+8;c.x[2]=(uint32_t)-1;
 mk8_shared_fonts(&c);return c.x[0]!=0 || memory[0]!=0xcc;
}
''', encoding='utf-8')
            exe = output/'fonts.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'runtime/mk8'), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_audio_render_info_revision_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            text = (runtime/'services/audio.c').read_text(encoding='utf-8')
            section = text[text.index('    oh[10] ='):text.index('    oh[15] = o;')]
            program = output/'audio-layout.c'
            program.write_text('''#include <stdint.h>
#include <string.h>
static int check(uint32_t revision, uint32_t capacity, uint32_t expected) {
    struct { uint32_t revision; } state = {revision}, *r = &state;
    struct { uint64_t size; } buffer = {capacity}, *out = &buffer;
    uint32_t oh[16] = {0}, o = 32;
    uint64_t elapsed = 0x12345678;
    unsigned char op[64]; memset(op, 0xcc, sizeof op);
'''+section+'''
    if (o != 32 + expected || oh[10] != expected) return 1;
    uint64_t got; memcpy(&got, op + 32, 8);
    if (expected && capacity >= 48) { if (got != elapsed) return 2; }
    else if (got != 0xccccccccccccccccull) return 3;
    return op[48] != 0xcc;
}
int main(void) {
    return check(0x34564552, 64, 0) || check(0x35564552, 64, 16) || check(0x35564552, 32, 16);
}
''', encoding='utf-8')
            exe = output/'audio-layout.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_no_mii_mode_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            program = output/'no-mii-test.c'
            program.write_text('#include "hle.h"\nvoid rt_log(const char *fmt, ...) { (void)fmt; }\n#include "mii/no_mii.c"\nint main(void) { return mk8_no_mii_renderer(); }\n', encoding='utf-8')
            exe = output/'no-mii-test.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'upstream/smm2recomp/recomp/runtime'), '-I'+str(ROOT/'runtime/mk8'), str(program), '-o', str(exe)], env=env, check=True)
            env.pop('MK8_EXPERIMENTAL_NO_MII', None)
            self.assertEqual(subprocess.run([str(exe)], env=env).returncode, 0)
            for value, expected in [('0', 0), ('true', 0), ('1', 1)]:
                self.assertEqual(subprocess.run([str(exe)], env=dict(env, MK8_EXPERIMENTAL_NO_MII=value)).returncode, expected)

    def test_packaged_texture_layout_unaligned_and_null(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            source = (runtime/'nvn/objects.c').read_text(encoding='utf-8')
            function = next(line for line in source.splitlines() if line.startswith('NVN(nvnTextureBuilderSetPackagedTextureLayout)'))
            program = output/'layout.c'
            program.write_text('#include "nvn/nvn.h"\n#include <string.h>\n#define TB(T, off) G(T, A(0), off)\n'+function+'''
int main(void) {
    unsigned char builder[128] __attribute__((aligned(8))) = {0};
    unsigned char packed[16] __attribute__((aligned(8))) = {0};
    uint64_t expected = 0x1122334455667788ull, actual = 0;
    memcpy(packed + 4, &expected, 8);
    Cpu cpu = {0}; cpu.x[0] = (uintptr_t)builder; cpu.x[1] = (uintptr_t)(packed + 4);
    hle_nvnTextureBuilderSetPackagedTextureLayout(&cpu);
    memcpy(&actual, builder + 0x50, 8); if (actual != expected) return 1;
    cpu.x[1] = 0; hle_nvnTextureBuilderSetPackagedTextureLayout(&cpu);
    memcpy(&actual, builder + 0x50, 8); return actual != 0;
}
''', encoding='utf-8')
            exe = output/'layout.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(runtime), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_vi_fit_layer_and_reject_unsupported_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            source = ROOT/'upstream/smm2recomp/recomp/runtime'
            program = output/'vi-test.c'
            program.write_text('''#include "hle.h"
#include <stdlib.h>
void rt_log(const char *fmt, ...) { (void)fmt; }
void rt_fatal_trace(void) { exit(87); }
#include "services/vi.c"
int main(int argc, char **argv) {
    (void)argv; Cpu c = {0}; c.x[0] = GUEST_LO + 0x1000; c.x[1] = argc > 1 ? 4 : 2;
    mk8_vi_scaling(&c); return c.x[0] != 0;
}
''', encoding='utf-8')
            exe = output/'vi-test.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(source), '-I'+str(ROOT/'runtime/mk8'), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)
            failed = subprocess.run([str(exe), 'unsupported'], capture_output=True)
            self.assertEqual(failed.returncode, 87)
            self.assertIn(b'not implemented', failed.stderr)

    def test_language_codes_in_four_byte_aligned_guest_buffer(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            source = (runtime/'services/hid_set.c').read_text(encoding='utf-8')
            functions = source[source.index('static const char *const languages[]'):source.index('SERVICE("set", set)')]
            program = output/'languages.c'
            program.write_text('#include "ipc.h"\n#include <string.h>\n'+functions+'''
int main(void) {
    unsigned char memory[160] __attribute__((aligned(8))) = {0};
    IpcReq req = {0}; IpcResp resp = {0};
    req.nb = 1; req.b[0].addr = (uintptr_t)(memory + 4); req.b[0].size = 144;
    language_list(&req, &resp, 18);
    int count = 0; memcpy(&count, resp.data, sizeof count);
    return count != 18 || memcmp(memory + 4, "ja", 2) || memcmp(memory + 4 + 17*8, "pt-BR", 5);
}
''', encoding='utf-8')
            exe = output/'languages.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(runtime), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_ipc_payload_alignment_and_serialized_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', output)
            program = output/'test.c'
            program.write_text('''#include "ipc.h"
#include <string.h>
_Static_assert(offsetof(IpcResp, data) % 8 == 0, "64-bit payload alignment");
int main(void) {
    IpcResp reply = {0}; IpcResp *resp = &reply;
    OUT(uint64_t, 0) = 0x1122334455667788ull;
    unsigned char wire[12] = {0};
    memcpy(wire, &resp->result, 4); memcpy(wire + 4, resp->data, 8);
    return wire[4] != 0x88 || wire[11] != 0x11;
}
''', encoding='utf-8')
            exe = output/'ipc-test.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(runtime), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
