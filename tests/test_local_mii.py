import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from native import DEFAULT_COMPILER


@unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
class LocalMiiTests(unittest.TestCase):
    def test_editor_persists_profile_and_cancel_preserves_it(self):
        source = (ROOT/'upstream/smm2recomp/recomp/runtime/services/mii.c').read_text(encoding='utf-8')
        default = source[source.index('void mii_default_charinfo'):source.index('static bool one_mii')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root/'editor.c'
            program.write_text('''#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#define LOG(...) ((void)0)
'''+default+'''
#include "mii/local_mii.inc"
#include "mii/mii_editor.inc"
static int confirm=1;
void menu_keyboard_title(const char *t) {}
void menu_notice(const char *t,double s) {}
int menu_keyboard_run(const char *i,int n,char *o,int cap) { snprintf(o,cap,"Jo\\xc3\\xa3o");return confirm; }
int main(int argc,char **argv) {
 assert(argc==2);_putenv_s("MK8_MII_PROFILE_PATH",argv[1]);
 _putenv_s("MK8_MII_ID","00112233445566778099aabbccddeeff");
 _putenv_s("MK8_MII_NAME","Player");_putenv_s("MK8_MII_COLOR","3");_putenv_s("MK8_MII_GENDER","0");
 uint8_t out[0x80], info[0x58];
 _putenv_s("MK8_MII_FACE","040203050201015a2d");
 assert(mk8_mii_editor(4,out)==0x80 && out[0]==0);
 assert(mk8_profile_charinfo(info) && !memcmp(info,out+4,0x58));
 assert(info[0x2e]==4 && info[0x31]==12 && info[0x29]==90 && info[0x4f]==1);
 assert(info[16]=='J' && info[20]==0xe3 && info[0x27]==3 && mk8_mii_updated);
 assert(!mk8_mii_save_name("",info));assert(!mk8_mii_save_name("           ",info));
 assert(!mk8_mii_save_name("12345678901",info));assert(!mk8_mii_save_name("A\\nB",info));
 confirm=0;assert(mk8_mii_editor(4,out)==0x80 && out[0]==1);
 assert(mk8_mii_editor(2,out)==0x20 && out[0]==1);
 assert(mk8_profile_charinfo(info) && info[16]=='J');
 return 0;
}
''', encoding='utf-8')
            exe = root/'editor.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'runtime/mk8'), str(program), '-lbcrypt', '-o', str(exe)], env=env, check=True)
            profile = root/'mii-profile.json'
            subprocess.run([str(exe), str(profile)], check=True)
            stored = json.loads(profile.read_text())
            self.assertEqual({k:stored['appearance'][k] for k in ['name','color','gender']}, {'name': 'Jo\u00e3o', 'color': 3, 'gender': 0})
            self.assertEqual(stored['appearance']['face'], {'skin':4,'shape':2,'hair':3,'hair_color':5,'eyes':2,'eye_color':1,'glasses':True,'height':90,'build':45})
            self.assertEqual(stored['id'], list(bytes.fromhex('00112233445566778099aabbccddeeff')))
            subprocess.run([str(exe), str(profile)], check=True)  # atomic replacement on Windows
            self.assertEqual(json.loads(profile.read_text()), stored)

    def test_profile_overlays_valid_identity_and_rejects_invalid_fields(self):
        source = (ROOT/'upstream/smm2recomp/recomp/runtime/services/mii.c').read_text(encoding='utf-8')
        default = source[source.index('void mii_default_charinfo'):source.index('static bool one_mii')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root/'mii.c'
            program.write_text('''#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
'''+default+'''
#include "mii/local_mii.inc"
int main(void) {
 uint8_t out[0x58], again[0x58];
 _putenv_s("MK8_MII_FACE", "");
 _putenv_s("MK8_MII_ID", "");assert(!mk8_profile_charinfo(out));
 _putenv_s("MK8_MII_ID", "00112233445566778099aabbccddeeff");
 _putenv_s("MK8_MII_NAME", "Luigi");_putenv_s("MK8_MII_COLOR", "3");_putenv_s("MK8_MII_GENDER", "0");
 assert(mk8_profile_charinfo(NULL));assert(mk8_profile_charinfo(out));assert(mk8_profile_charinfo(again));
 assert(!memcmp(out,again,sizeof out));
 _putenv_s("MK8_MII_FACE","ff0203050201015a2d");assert(!mk8_profile_charinfo(out));
 _putenv_s("MK8_MII_FACE","040203050201015a2d");assert(mk8_profile_charinfo(out));assert(out[0x2e]==4 && out[0x31]==12);
 _putenv_s("MK8_MII_FACE","");assert(mk8_profile_charinfo(out));assert(out[0x27]==3 && out[0x28]==0);
 assert(out[16]=='L' && out[18]=='u' && out[8]==0x80 && out[15]==0xff);
 uint8_t latest[0x58], saved[0x58];memcpy(saved,out,0x58);
 assert(mk8_mii_latest(saved,1,true,latest)==3);                                // same Mii: NotUpdated
 saved[16]='X';assert(mk8_mii_latest(saved,1,true,latest)==0 && !memcmp(latest,out,0x58));  // edited: latest
 saved[0]^=1;assert(mk8_mii_latest(saved,1,true,latest)==4);                    // another create ID: NotFound
 assert(mk8_mii_latest(out,2,true,latest)==4 && mk8_mii_latest(out,1,false,latest)==4);
 uint8_t list[7*0x5c+4];memset(list,0xcc,sizeof list);
 assert(mk8_mii_count(1,true)==1 && mk8_mii_count(2,true)==6 && mk8_mii_count(3,true)==7);
 assert(mk8_mii_count(1,false)==0 && mk8_mii_count(0,true)==0);
 assert(mk8_mii_get(list,7*0x5c,0x5c,3,true)==7);
 assert(!memcmp(list,out,0x58) && list[0x58]==0);
 for(int i=1;i<7;i++)assert(list[i*0x5c+0x58]==1 && list[i*0x5c+15]==7+i-1);
 assert(list[7*0x5c]==0xcc);
 memset(list,0xcc,sizeof list);assert(mk8_mii_get(list,0x57,0x58,3,true)==0 && list[0]==0xcc);
 assert(mk8_mii_get(list,0x58,0x58,2,true)==1 && list[0x58]==0xcc);
 _putenv_s("MK8_MII_COLOR", "12");assert(!mk8_profile_charinfo(out));
 _putenv_s("MK8_MII_COLOR", "3x");assert(!mk8_profile_charinfo(out));
 _putenv_s("MK8_MII_COLOR", "11");_putenv_s("MK8_MII_GENDER", "2");assert(!mk8_profile_charinfo(out));
 _putenv_s("MK8_MII_GENDER", "1");assert(mk8_profile_charinfo(out));assert(out[0x28]==1 && out[0x27]==11);
 _putenv_s("MK8_MII_ID", "00112233445566770099aabbccddeeff");assert(!mk8_profile_charinfo(out));
 _putenv_s("MK8_MII_ID", "0011");assert(!mk8_profile_charinfo(out));
 return 0;
}
''', encoding='utf-8')
            exe = root/'mii.exe'
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT/'local/native-tools/cache'))
            subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-UNDEBUG', '-I'+str(ROOT/'runtime/mk8'), str(program), '-o', str(exe)], env=env, check=True)
            subprocess.run([str(exe)], check=True)
