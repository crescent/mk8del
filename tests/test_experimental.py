import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'toolchain'))
import experimental
from native import DEFAULT_COMPILER


class ExperimentalTests(unittest.TestCase):
    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_no_mii_preparation_keeps_database_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.UNIT
            src.parent.mkdir()
            raw = b'''#include <stdint.h>
#include <assert.h>
#include <string.h>
typedef struct { uint64_t x[32]; } Cpu;
static int mode, models, reloads;
static uint64_t child;
int mk8_no_mii_renderer(void) { return mode; }
static uint64_t LD64(uint64_t a) { uint64_t n;memcpy(&n,(void *)(uintptr_t)a,8);return n; }
void f_700080e1d8(Cpu *c) { assert(c->x[0]==child);reloads++; }
void f_700080fa44(Cpu *c) { models++; }
void f_7000810278(Cpu *c) { models++; }
void f_7000812fc0(Cpu *c) { models++; }
static int requests, panes;
void f_700080f4a4(Cpu *c) { requests++; c->x[0] = 77; }
void f_7000810c90(Cpu *c) { panes++; }
int main(void) {
 uint64_t engine[0x560/8] = {0}, workers = 5;
 for(mode=0;mode<=1;mode++) for(int ready=0;ready<=1;ready++) {
  Cpu r={0};engine[0x548/8]=ready ? (uint64_t)(uintptr_t)&workers : 0;r.x[0]=(uint64_t)(uintptr_t)engine;requests=0;
  f_700080f4a4(&r);int skipped=mode && !ready;assert(requests==!skipped && r.x[0]==(skipped ? 0 : 77));
  Cpu q={0};panes=0;f_7000810c90(&q);assert(panes==!mode);
  q.x[0]=1;panes=0;f_7000810c90(&q);assert(panes==1);
 }
 uint64_t parent[12]={0}, database=0;child=(uint64_t)(uintptr_t)&database;parent[11]=child;
 Cpu c={0};
 for(mode=0;mode<=1;mode++) {
  models=reloads=0;c.x[0]=(uint64_t)(uintptr_t)parent;c.x[19]=123;
  f_700080fa44(&c);assert(models==!mode && reloads==mode && c.x[19]==123);
 }
 return 0;
}
'''
            src.write_bytes(raw)
            with patch.object(experimental, 'SOURCE_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            exe = root/'reload.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(result), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_confirmation_keeps_profile_and_omits_paired_portrait_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MII_CONFIRM_UNIT
            src.parent.mkdir()
            raw = b'''#include <stdint.h>
#include <assert.h>
typedef struct { uint64_t x[32]; } Cpu;
static int mode, requests, profiles, transitions;
int mk8_no_mii_renderer(void) { return mode; }
void f_70008104d4(Cpu *c) { requests++; }
static void confirm(Cpu *c) {
 profiles++;
 c->x[30] = 0x70004d7530ull; f_70008104d4(c);
L_70004d7694:;
    if (((c->x[0] >> 0) & 1) == 0) { goto L_70004d76a4; }
 transitions++;
L_70004d76a4:; return;
}
int main(void) {
 Cpu c={0};
 for(mode=0;mode<=1;mode++)for(int ready=0;ready<=1;ready++) {
  requests=profiles=transitions=0;c.x[0]=ready;c.x[19]=789;confirm(&c);
  assert(requests==!mode && profiles==1 && transitions==(mode||ready));
  assert(c.x[0]==ready && c.x[19]==789);
 }
 return 0;
}
'''
            raw = raw.replace(b'\n', b'\r\n')
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'MII_CONFIRM_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            exe = root/'confirmation.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(result), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_selection_transitions_without_claiming_a_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MII_SELECTION_UNIT
            src.parent.mkdir()
            raw = b'''#include <stdint.h>
#include <assert.h>
typedef struct { uint64_t x[32]; } Cpu;
static int mode, requests;
int mk8_no_mii_renderer(void) { return mode; }
void f_700080f47c(Cpu *c) { requests++; }
static int first(Cpu *c) {
 c->x[30] = 0x70004afd2cull; f_700080f47c(c);
L_70004afc54:;
    if (((c->x[0] >> 0) & 1) == 0) { goto L_70004afc60; }
 return 2;
L_70004afc60:; return 1;
}
static int second(Cpu *c) {
L_70004afcd4:;
    if (((c->x[0] >> 0) & 1) == 0) { goto L_70004aff30; }
 return 2;
L_70004aff30:; return 0;
}
int main(void) {
 Cpu c={0};
 for(mode=0;mode<=1;mode++)for(int ready=0;ready<=1;ready++) {
  c.x[0]=ready;c.x[19]=789;requests=0;
  assert(first(&c)==(mode||ready?2:1));
  assert(requests==!mode);
  assert(second(&c)==(mode||ready?2:0));
  assert(c.x[0]==ready && c.x[19]==789);
 }
 return 0;
}
'''
            raw = raw.replace(b'\n', b'\r\n')
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'MII_SELECTION_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            exe = root/'selection.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(result), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_dialog_omits_only_missing_portrait_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MII_DIALOG_UNIT
            src.parent.mkdir()
            raw = b'''#include <stdint.h>
#include <assert.h>
typedef struct { uint64_t x[32]; } Cpu;
static int mode, prior_busy;
int mk8_no_mii_renderer(void) { return mode; }
static void dialog(Cpu *c) {
 if(prior_busy){c->x[0]=1;return;}
L_70004018a0:;
 c->x[0]=(c->x[8]==0x21);
L_700040194c:;
 return;
}
int main(void) {
 Cpu c={0};
 for(mode=0;mode<=1;mode++)for(prior_busy=0;prior_busy<=1;prior_busy++)for(int id=0x20;id<=0x22;id++) {
  c.x[8]=id;c.x[19]=789;dialog(&c);
  assert(c.x[0]==(prior_busy||(!mode&&id==0x21)));assert(c.x[19]==789);
 }
 return 0;
}
'''
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'MII_DIALOG_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            exe = root/'dialog.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(result), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_worker_and_lookup_leave_models_absent_only_in_experimental_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MII_WORKER_UNIT
            src.parent.mkdir()
            raw = b'''#include <stdint.h>
#include <assert.h>
typedef struct { uint64_t x[32]; } Cpu;
static int mode, requests;
int mk8_no_mii_renderer(void) { return mode; }
#define ST32(a,v) (*(uint32_t *)(uintptr_t)(a)=(v))
void f_700041a6b0(Cpu *c) { if (!c->x[3]) return; requests++; }
void f_700041a834(Cpu *c) { c->x[0] = 0x1234; }
void f_700041b2f0(Cpu *c) { requests++; }
void f_700041cb5c(Cpu *c) { requests++; }
void lobby_worker(Cpu *c) { c->x[19]++; c->x[30] = 0x700041c000ull; f_700041cb5c(c); c->x[19]++; }
int main(void) {
 Cpu c={0};
 for(mode=0;mode<=1;mode++) {
  requests=0;c.x[3]=123;f_700041a6b0(&c);assert(requests==!mode);
  c.x[0]=456;c.x[19]=789;f_700041a834(&c);
  assert(c.x[0]==(mode?0:0x1234));assert(c.x[19]==789);
  requests=0;lobby_worker(&c);assert(requests==!mode && c.x[19]==791);c.x[19]=789;
  for(unsigned group=0;group<4;group++)for(unsigned slot=0;slot<14;slot++) {
   uint32_t queue[0x4e0/4];for(unsigned i=0;i<sizeof queue/4;i++)queue[i]=0x12345678;
   c.x[0]=(uintptr_t)queue;c.x[1]=group;c.x[2]=slot;requests=0;f_700041b2f0(&c);
   unsigned changed=((group<3?group:0)*0x1a0+(slot<13?slot:0)*0x20)/4;
   for(unsigned i=0;i<sizeof queue/4;i++)assert(queue[i]==((mode && i==changed)?0:0x12345678));
   assert(requests==!mode && c.x[19]==789 && c.x[0]==(uintptr_t)queue);
  }
 }
 return 0;
}
'''
            src.write_bytes(raw)
            with patch.object(experimental, 'MII_WORKER_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            exe = root/'worker.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(result), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_worker_selects_existing_empty_character_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MII_WORKER_UNIT
            src.parent.mkdir()
            raw = b'void f_700041a6b0(Cpu *c) { if (!c->x[3]) return; request_model(c); }\nvoid f_700041a834(Cpu *c) { lookup_model(c); }\nvoid f_700041b2f0(Cpu *c) { request_portrait(c); }\nvoid lobby_worker(Cpu *c) { c->x[30] = 0x700041c000ull; f_700041cb5c(c); }\n'
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'MII_WORKER_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            self.assertIn('if (mk8_no_mii_renderer()) c->x[3] = 0;', result.read_text())
            self.assertIn('if (!c->x[3]) return; request_model(c);', result.read_text())
            src.write_bytes(raw+raw)
            with patch.object(experimental, 'MII_WORKER_SHA256', hashlib.sha256(raw+raw).hexdigest()):
                with self.assertRaises(ValueError):
                    experimental.patch_no_mii_unit(src, root/'out')

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_empty_mii_policy_execution_preserves_nonempty_and_normal_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.UI_UNIT
            src.parent.mkdir()
            raw = b'''#include <stdint.h>
#include <string.h>
typedef struct { uint64_t x[32]; } Cpu;
static int mode;
int mk8_no_mii_renderer(void) { return mode; }
static uint32_t LD32(uint64_t p) { uint32_t n; memcpy(&n,(void *)(uintptr_t)p,4); return n; }
void f_70004297b4(Cpu *c) { c->x[0] = 12; }
void f_70004297bc(Cpu *c) { c->x[0] = 1; }
void f_70004297cc(Cpu *c) { c->x[0] = 1; }
void f_7000428b88(Cpu *c) { c->x[0] = 99; }
void f_7000428a34(Cpu *c) { c->x[1]++; }
void f_7000428880(Cpu *c) { c->x[1]++; }
void f_70004288a0(Cpu *c) { c->x[1]++; }
void f_7000428900(Cpu *c) { c->x[1]++; }
void f_70004289ac(Cpu *c) { c->x[1]++; }
void f_70004289d0(Cpu *c) { c->x[1]++; }
void f_7000428a18(Cpu *c) { c->x[1]++; }
void f_7000428d94(Cpu *c) { c->x[1]++; }
void f_7000428f80(Cpu *c) { c->x[1]++; }
void f_7000428fb0(Cpu *c) { c->x[1]++; }
void f_7000428810(Cpu *c) { c->x[1]++; }
void f_7000428838(Cpu *c) { c->x[1]++; }
void f_70004288bc(Cpu *c) { c->x[0] = 1; }
void f_7000428720(Cpu *c) { c->x[0] = 1; }
int main(void) {
 uint32_t manager[16]={0}; Cpu c={0};
 for(mode=0;mode<=1;mode++) {
  f_70004297b4(&c); if(c.x[0]!=(mode?0:12))return 1;
  f_70004297bc(&c); if(c.x[0]!=(mode?0:1))return 2;
  f_70004297cc(&c); if(c.x[0]!=(mode?0:1))return 3;
  for(unsigned a=0;a<2;a++)for(unsigned b=0;b<2;b++) {
   manager[0x18/4]=a;manager[0x28/4]=b;c.x[0]=(uintptr_t)manager;
   uint64_t object=c.x[0];
   f_7000428b88(&c);if(c.x[0]!=(mode&&!a&&!b?1:99))return 4;
   c.x[0]=object;c.x[1]=0;f_7000428a34(&c);
   if(c.x[1]!=(mode&&!a&&!b?0:1))return 5;
   f_7000428880(&c);f_70004288a0(&c);f_7000428900(&c);
   if(c.x[1]!=(mode&&!a&&!b?0:4))return 7;
   f_70004289ac(&c);f_70004289d0(&c);f_7000428a18(&c);
   if(c.x[1]!=(mode&&!a&&!b?0:7))return 8;
   f_7000428d94(&c);f_7000428f80(&c);f_7000428fb0(&c);
   if(c.x[1]!=(mode&&!a&&!b?0:10))return 9;
   f_7000428810(&c);f_7000428838(&c);
   if(c.x[1]!=(mode&&!a&&!b?0:12))return 11;
   c.x[0]=object;f_70004288bc(&c);if(c.x[0]!=(mode&&!a&&!b?0:1))return 6;
   c.x[0]=object;f_7000428720(&c);if(c.x[0]!=(mode&&!a&&!b?0:1))return 10;
  }
 }
 return 0;
}
'''
            src.write_bytes(raw)
            with patch.object(experimental, 'UI_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            exe = root/'policy.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', str(result), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)

    def test_mii_heap_preparation_is_optional_and_hash_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MII_HEAP_UNIT
            src.parent.mkdir()
            raw = b'void f_70003e0ea0(Cpu *c) { prepare_models(c); }\n'
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'MII_HEAP_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            self.assertIn('if (mk8_no_mii_renderer()) return;', result.read_text())
            self.assertIn('prepare_models(c);', result.read_text())
            for invalid in (b'unrelated', raw+raw):
                src.write_bytes(invalid)
                with patch.object(experimental, 'MII_HEAP_SHA256', hashlib.sha256(invalid).hexdigest()):
                    with self.assertRaises(ValueError):
                        experimental.patch_no_mii_unit(src, root/'out')

    def test_model_part_guard_is_hash_bounded_and_skips_only_null(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.MODEL_PART_UNIT
            src.parent.mkdir()
            raw = b'void f_700069127c(Cpu *c) {\n    update_part(c);\n}\n'
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'MODEL_PART_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out').read_text()
            self.assertEqual(src.read_bytes(), raw)
            self.assertIn('if (!c->x[1] && mk8_no_mii_null_part(c->x[0])) return;\n    update_part(c);', result)
            for invalid in (b'unrelated', raw+raw):
                src.write_bytes(invalid)
                with patch.object(experimental, 'MODEL_PART_SHA256', hashlib.sha256(invalid).hexdigest()):
                    with self.assertRaises(ValueError):
                        experimental.patch_no_mii_unit(src, root/'out')

    def test_ui_model_policy_preserves_normal_path_and_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.UI_UNIT
            src.parent.mkdir()
            raw = b'void f_70004297b4(Cpu *c) { c->x[0] = 12; return; }\nvoid f_70004297bc(Cpu *c) { c->x[0] = 1; return; }\nvoid f_70004297cc(Cpu *c) { c->x[0] = 1; return; }\n'
            raw += b'void f_7000428b88(Cpu *c) { check_mii_slots(c); }\n'
            raw += b'void f_7000428a34(Cpu *c) { animate_mii_slots(c); }\n'
            for address in ('7000428880', '70004288a0', '7000428900',
                            '70004289ac', '70004289d0', '7000428a18',
                            '7000428d94', '7000428f80', '7000428fb0',
                            '7000428810', '7000428838'):
                raw += f'void f_{address}(Cpu *c) {{ animate_mii_slots(c); }}\n'.encode()
            raw += b'void f_70004288bc(Cpu *c) { query_mii_pose(c); }\n'
            raw += b'void f_7000428720(Cpu *c) { query_mii_pose(c); }\n'
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'UI_SHA256', hashlib.sha256(raw).hexdigest()):
                result = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            text = result.read_text()
            self.assertIn('if (mk8_no_mii_renderer()) { c->x[0] = 0; return; }', text)
            self.assertEqual(text.count('if (mk8_no_mii_renderer())'), 3)
            self.assertIn('LD32(c->x[0] + 0x18) == 0 && LD32(c->x[0] + 0x28) == 0', text)
            self.assertIn('check_mii_slots(c);', text)
            self.assertIn('c->x[0] = 12; return;', text)
            self.assertIn('c->x[0] = 1; return;', text)
            src.write_bytes(raw+raw)
            with patch.object(experimental, 'UI_SHA256', hashlib.sha256(raw+raw).hexdigest()):
                with self.assertRaises(ValueError):
                    experimental.patch_no_mii_unit(src, root/'out')

    def test_heap_lock_requires_verified_constructor_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.HEAP_UNIT
            src.parent.mkdir()
            raw = (experimental.HEAP_SELECT+'\n').encode()
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'HEAP_SHA256', hashlib.sha256(raw).hexdigest()):
                output = experimental.patch_no_mii_unit(src, root/'out')
            text = output.read_text(encoding='utf-8')
            self.assertNotIn('COND_NE', text)
            self.assertIn('c->x[8] = (uint32_t)c->x[9];', text)
            self.assertIn('ST16(ea, (uint32_t)c->x[8]);', text)
            self.assertEqual(src.read_bytes(), raw)
            for invalid in ('unrelated', experimental.HEAP_SELECT*2):
                src.write_bytes(invalid.encode())
                with patch.object(experimental, 'HEAP_SHA256', hashlib.sha256(invalid.encode()).hexdigest()):
                    with self.assertRaises(ValueError):
                        experimental.patch_no_mii_unit(src, root/'out')

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_guest_longjmp_resumes_in_recompiled_frame(self):
        # Synthetic unit shaped like the generated code: guest setjmp stub, a callee whose guest longjmp branches to
        # the return address, and the real runtime resume (runtime/mk8/support/guest_setjmp.c).
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'inc').mkdir()
            (root/'inc'/'cpu.h').write_text('#pragma once\n#include <stdint.h>\ntypedef struct { uint64_t x[32]; uint64_t sp, pc; } Cpu;\n')
            (root/'inc'/'rt.h').write_text('''#pragma once
#include "cpu.h"
#include <stdio.h>
#include <stdlib.h>
typedef void (*HostFn)(Cpu *c);
void hle_override(uint64_t addr, HostFn fn, const char *name);
#define LOG(...) ((void)0)
#define FATAL(...) (fprintf(stderr, __VA_ARGS__), exit(3))
''')
            src = root/'sdk'/'sdk_003.c'
            src.parent.mkdir()
            raw = b'''#include "cpu.h"
#include <stdlib.h>
#include <string.h>
typedef void (*HostFn)(Cpu *c);
static struct { uint64_t addr; HostFn fn; } ov[8]; static int nov;
void hle_override(uint64_t addr, HostFn fn, const char *name) { (void)name; ov[nov].addr = addr; ov[nov++].fn = fn; }
static void dispatch(Cpu *c) { for (int i = 0; i < nov; i++) if (ov[i].addr == c->pc) { ov[i].fn(c); return; } exit(4); }
static uint64_t env[4];
void f_70043e9b50(Cpu *c) { env[0] = c->x[19]; env[1] = c->sp; env[2] = c->x[30]; c->x[0] = 0; }
__attribute__((noinline)) static void guest_longjmp(Cpu *c, int depth) {
    if (depth) { guest_longjmp(c, depth - 1); return; }
    c->x[19] = env[0]; c->sp = env[1]; c->x[30] = env[2]; c->x[0] = 9; c->pc = c->x[30]; dispatch(c);
    exit(5);  // a guest longjmp never returns here
}
void f_encode(Cpu *c) {
    c->x[19] = 0x1234;
    c->x[30] = 0x700402e874ull; f_70043e9b50(c);
    if ((uint32_t)c->x[0] == 0) { goto L_body; }
    c->x[1] = 0xe0 + c->x[0];  // error path: longjmp value with the restored callee-saved register
    return;
L_body:;
    c->x[19] = 0xdead;
    if (c->x[2]) guest_longjmp(c, 6);
    c->x[1] = 0x600d;
}
int main(int argc, char **argv) {
    Cpu c = {0}; c.sp = 0x7fff0000;
    c.x[2] = 0; f_encode(&c); if (c.x[1] != 0x600d) return 1;
    c.x[2] = 1; f_encode(&c); if (c.x[1] != 0xe9 || c.x[19] != 0x1234 || c.sp != 0x7fff0000) return 2;
    (void)argv; if (argc > 1) { c.pc = 0x700402e874ull; c.sp = 0x7ffe0000; dispatch(&c); }  // no matching setjmp: must stop
    return 0;
}
'''
            src.write_bytes(raw)
            sites = {'sdk_003.c': (hashlib.sha256(raw).hexdigest(), ('700402e874',))}
            with patch.object(experimental, 'SETJMP_SITES', sites):
                result = experimental.patch_no_mii_unit(src, root/'out')
                other = root/'main'/'sdk_003.c'
                self.assertEqual(experimental.patch_no_mii_unit(other, root/'out'), other)
                src.write_bytes(raw + b'\n')
                with self.assertRaises(ValueError):
                    experimental.patch_no_mii_unit(src, root/'out')
            self.assertIn('if (!setjmp(*mk8_jb))', result.read_text(encoding='utf-8'))
            runtime = Path(__file__).resolve().parents[1]/'runtime/mk8/support/guest_setjmp.c'
            exe = root/'setjmp.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(__file__).resolve().parents[1]/'local/native-tools/cache'))
            for level in ('-O1', '-O2'):
                subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', level, '-I'+str(root/'inc'),
                                str(result), str(runtime), '-o', str(exe)], env=env, check=True)
                subprocess.run([str(exe)], check=True)
                self.assertEqual(subprocess.run([str(exe), 'stale'], capture_output=True).returncode, 3)

    def test_hook_is_bounded_and_preserves_original_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root/'main'/experimental.UNIT
            src.parent.mkdir()
            raw = b'void f_700080fa44(Cpu *c) { original(c); }\nvoid f_7000810278(Cpu *c) { update(c); }\nvoid f_7000812fc0(Cpu *c) { notify(c); }\nvoid f_700080f4a4(Cpu *c) { request(c); }\nvoid f_7000810c90(Cpu *c) { pane(c); }\n'
            src.write_bytes(raw)
            with self.assertRaises(ValueError):
                experimental.patch_no_mii_unit(src, root/'out')
            with patch.object(experimental, 'SOURCE_SHA256', hashlib.sha256(raw).hexdigest()):
                output = experimental.patch_no_mii_unit(src, root/'out')
            self.assertEqual(src.read_bytes(), raw)
            text = output.read_text(encoding='utf-8')
            self.assertIn('if (mk8_no_mii_renderer()) return;', text)
            self.assertIn('original(c);', text)
            self.assertIn('if (!c->x[0] && mk8_no_mii_renderer()) return;', text)
            self.assertIn('notify(c);', text)
            self.assertIn('!LD64(c->x[0] + 0x548)) { c->x[0] = 0; return; }', text)
            self.assertIn('request(c);', text)
            self.assertIn('pane(c);', text)
            other = root/'sdk'/'sdk_001.c'
            self.assertEqual(experimental.patch_no_mii_unit(other, root/'out'), other)
