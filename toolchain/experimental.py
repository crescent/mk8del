"""Bounded hooks for the verified MK8 4.0.1 diagnostic build."""
import hashlib

UNIT = 'main_177.c'
SOURCE_SHA256 = 'b8217ac591523fc1c2433dc9306a60a7947ab7bd0d767fd174d250bd66848726'
HEAP_UNIT = 'main_140.c'
HEAP_SHA256 = 'c16d3488839dccc18874adb1e8d0f2a75a4bd5c0d5dc5b9b3e9f56db4221d0c1'
UI_UNIT = 'main_085.c'
UI_SHA256 = 'bfea2feabe753e5da3e303318230d9055a5296ebc50502c1f01095fc2a2810a4'
MII_HEAP_UNIT = 'main_078.c'
MII_HEAP_SHA256 = 'bf737d1f150f5a61d442c88b2b2b24abd11e3d5e5d8225648c59503db99f703e'
MII_WORKER_UNIT = 'main_084.c'
MII_WORKER_SHA256 = '5951e724de88abddbbfa88d33550b6d214816134ac1000411f8b2f0937f9a5bd'
MII_DIALOG_UNIT = 'main_082.c'
MII_DIALOG_SHA256 = 'c6d46af3e71b558fa38894d3b26de9569671c93a026f7a0cce99eb2e16707082'
MII_SELECTION_UNIT = 'main_102.c'
MII_SELECTION_SHA256 = '1e6840dbc6c84ece86afc678dc967981c168de1c27bc3c27a824c46969d8b331'
MII_CONFIRM_UNIT = 'main_113.c'
MII_CONFIRM_SHA256 = '4b4cf4a1aec1b9d1d0479a920a3fbf46fe518d23d7624a19caea00808f7d88f7'
MODEL_PART_UNIT = 'main_149.c'
MODEL_PART_SHA256 = 'b9358ba936276cdcc43b7126de8d20af83ee2b4e2d0bb337429e7609c3b9939e'
LAN_VOTE_UNIT = 'main_115.c'
LAN_VOTE_SHA256 = 'e7f979c9bae346fca7da9f76349d27eeec5fcc4b23c87b43d396c39b605ef048'
# Guest `bl setjmp` sites (PLT stub f_70043e9b50 -> sdk GOT +0x9875a8 "setjmp") by generated sdk unit; each address is
# the call's return address, which guest longjmp branches to. runtime/mk8/support/guest_setjmp.c lists the same ones.
SETJMP_STUB = 'f_70043e9b50'
SETJMP_SITES = {
    'sdk_003.c': ('d4bd6c5016adf8994e4dc033a44a4baeee47a87b1d7f14fbf58ddc55ef5e5599', ('700402e874', '700402efe0')),
    'sdk_053.c': ('d1c8e37c21470c201c5959a0239a822bebc65ea75062757c46754debf4cb91f0', ('70041f8928',)),
    'sdk_115.c': ('c072a5f919d881153db63e0d2a9384941ccee269bc06f7d5928b07f2749a0bfb', ('70043e7188',)),
}
HEAP_SELECT ='c->x[8] = (uint32_t)((COND_NE(c)) ? (uint32_t)c->x[9] : (uint32_t)((uint32_t)c->x[8]));\n    { uint64_t ea = c->x[19] + 0xd0ull; ST16(ea, (uint32_t)c->x[8]); }'


def patch_setjmp_unit(src, output):
    expected, resumes = SETJMP_SITES[src.name]
    raw = src.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Guest setjmp sites require the verified MK8 4.0.1 generated sdk unit')
    text = raw.decode('utf-8').replace('\r\n', '\n')
    header = '#include "cpu.h"\n'
    if not text.startswith(header):
        raise ValueError('Generated sdk unit header changed')
    for resume in resumes:
        call = f'    c->x[30] = 0x{resume}ull; {SETJMP_STUB}(c);\n'
        if text.count(call) != 1:
            raise ValueError('Guest setjmp call boundary changed')
        # The host setjmp lives in this function's frame, which stays live while guest code may longjmp to it.
        # (CRT setjmp: __builtin_setjmp hangs on x86_64-windows-gnu.)
        text = text.replace(call, f'    {{ jmp_buf *mk8_jb = mk8_setjmp_arm(c, 0x{resume}ull, __builtin_frame_address(0)); '
                                  f'if (!setjmp(*mk8_jb)) {{ c->x[30] = 0x{resume}ull; {SETJMP_STUB}(c); }} }}\n')
    text = header + '#include <setjmp.h>\nextern jmp_buf *mk8_setjmp_arm(Cpu *c, uint64_t resume, void *frame);\n' + text[len(header):]
    target = output/'experimental'/'sdk'/src.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return target


def patch_no_mii_unit(src, output):
    if src.parent.name == 'sdk' and src.name in SETJMP_SITES:
        return patch_setjmp_unit(src, output)
    if src.name == LAN_VOTE_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != LAN_VOTE_SHA256:
            raise ValueError('LAN vote policy requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8').replace('\r\n', '\n')
        # Omit the absent model's request and animation waits together. The vote's
        # received/valid-slot/busy checks and normal completion bookkeeping stay intact.
        changes = [
            ('void f_70004e4370(Cpu *c) {',
             'void f_70004e4370(Cpu *c) {\n    int mk8_skip_vote_mii = 0;'),
            ('c->x[30] = 0x70004e43dcull; f_7000427e6c(c);', '''{
        uint64_t models = LD64(c->x[0] + 0x68);
        mk8_skip_vote_mii = mk8_no_mii_renderer() && models &&
            LD32(models + 0x18) == 0 && LD32(models + 0x28) == 0;
        c->x[30] = 0x70004e43dcull;
        if (!mk8_skip_vote_mii) f_7000427e6c(c);
    }'''),
            ('    c->x[8] = (uint32_t)((uint32_t)c->x[0] ^ 0x1ull);\n    if (((c->x[8] >> 0) & 1) != 0) { goto L_70004e44b0; }',
             '    c->x[8] = (uint32_t)((uint32_t)c->x[0] ^ 0x1ull);\n    if (!mk8_skip_vote_mii && ((c->x[8] >> 0) & 1) != 0) { goto L_70004e44b0; }'),
            ('c->x[30] = 0x70004e4490ull; f_7000427e74(c);',
             'c->x[30] = 0x70004e4490ull; if (!mk8_skip_vote_mii) f_7000427e74(c);'),
            ('L_70004e44b0:;',
             'L_70004e44b0:;\n    if (mk8_skip_vote_mii && LD32(c->x[19] + 0x20) == 1) goto L_70004e44e4;'),
            # Online rooms take the other branch (f_700007c930 false): f_7000427888 keeps its bookkeeping, but its
            # answer is the absent model's pose (always false), which held the vote at "waiting for everyone" until
            # the room dropped this player (2026-11-12). f_7000427960 animates that model (f_7000428764, unguarded).
            ('c->x[30] = 0x70004e43e8ull; f_7000427888(c);', '''{
        uint64_t models = LD64(c->x[0] + 0x68);
        mk8_skip_vote_mii = mk8_no_mii_renderer() && models &&
            LD32(models + 0x18) == 0 && LD32(models + 0x28) == 0;
        c->x[30] = 0x70004e43e8ull; f_7000427888(c);
    }'''),
            ('c->x[30] = 0x70004e44a4ull; f_7000427960(c);',
             'c->x[30] = 0x70004e44a4ull; if (!mk8_skip_vote_mii) f_7000427960(c);'),
        ]
        for old, new in changes:
            if text.count(old) != 1:
                raise ValueError('LAN vote animation boundary changed')
            text = text.replace(old, new)
        if text.count('L_70004e44e4:;') != 1:
            raise ValueError('LAN vote completion boundary changed')
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == MII_CONFIRM_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MII_CONFIRM_SHA256:
            raise ValueError('Mii confirmation requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8').replace('\r\n', '\n')
        request = 'c->x[30] = 0x70004d7530ull; f_70008104d4(c);'
        wait = 'L_70004d7694:;\n    if (((c->x[0] >> 0) & 1) == 0) { goto L_70004d76a4; }'
        if text.count(request) != 1 or text.count(wait) != 1:
            raise ValueError('Mii confirmation boundary changed')
        # Preserve CharInfo copying and screen state; omit its portrait request and wait together.
        text = text.replace(request, 'c->x[30] = 0x70004d7530ull; if (!mk8_no_mii_renderer()) f_70008104d4(c);')
        text = text.replace(wait, wait.replace('if (', 'if (!mk8_no_mii_renderer() && ', 1))
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == MII_SELECTION_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MII_SELECTION_SHA256:
            raise ValueError('Mii selection wait requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8').replace('\r\n', '\n')
        request = 'c->x[30] = 0x70004afd2cull; f_700080f47c(c);'
        if text.count(request) != 1:
            raise ValueError('Mii post-editor portrait request boundary changed')
        # The editor already returned CharInfo; this call allocates its portrait, not the profile.
        text = text.replace(request, 'c->x[30] = 0x70004afd2cull; if (!mk8_no_mii_renderer()) f_700080f47c(c);')
        # These two transitions wait only for portraits. Keep model lookup nullable.
        for address, destination in [('70004afc54', '70004afc60'), ('70004afcd4', '70004aff30')]:
            boundary = f'L_{address}:;\n    if (((c->x[0] >> 0) & 1) == 0) {{ goto L_{destination}; }}'
            if text.count(boundary) != 1:
                raise ValueError('Mii selection wait boundary changed')
            text = text.replace(boundary, boundary.replace('if (', 'if (!mk8_no_mii_renderer() && ', 1))
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == MII_DIALOG_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MII_DIALOG_SHA256:
            raise ValueError('Mii dialog wait requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8')
        boundary = 'L_70004018a0:;'
        if text.count(boundary) != 1 or text.count('L_700040194c:;') != 1:
            raise ValueError('Mii dialog wait boundary changed')
        # Keep prior transition waits; omit only dialog 0x21's wait for an absent portrait.
        text = text.replace(boundary, boundary+'\n    if (mk8_no_mii_renderer() && (uint32_t)c->x[8] == 0x21) { c->x[0] = 0; goto L_700040194c; }')
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == MII_WORKER_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MII_WORKER_SHA256:
            raise ValueError('Mii worker policy requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8')
        entry = 'void f_700041a6b0(Cpu *c) {'
        if text.count(entry) != 1:
            raise ValueError('Mii worker boundary changed')
        # Select its existing null-character path: no model request or completion wait.
        text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer()) c->x[3] = 0;')
        portrait = 'c->x[30] = 0x700041c000ull; f_700041cb5c(c);'
        if text.count(portrait) != 1:
            raise ValueError('LAN lobby portrait worker boundary changed')
        # This worker calls the portrait allocator and waits for it. Keep queue bookkeeping,
        # but omit the graphics task when its renderer is absent; no texture is fabricated.
        text = text.replace(portrait, 'c->x[30] = 0x700041c000ull; if (!mk8_no_mii_renderer()) f_700041cb5c(c);')
        # The menu asks for the Mii model even with no slots. Its callers handle null.
        entry = 'void f_700041a834(Cpu *c) {'
        if text.count(entry) != 1:
            raise ValueError('Mii model lookup boundary changed')
        text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer()) { c->x[0] = 0; return; }')
        entry = 'void f_700041b2f0(Cpu *c) {'
        if text.count(entry) != 1:
            raise ValueError('Race portrait job boundary changed')
        # Race loading enqueues portraits even for Mario. Cancel only that graphics job:
        # state 0 is idle (queried by 0x41b824); texture storage remains absent.
        text = text.replace(entry, entry+'''
    if (mk8_no_mii_renderer()) {
        uint32_t group = (uint32_t)c->x[1], slot = (uint32_t)c->x[2];
        if (group >= 3) group = 0;
        if (slot >= 13) slot = 0;
        ST32(c->x[0] + (uint64_t)group * 0x1a0 + (uint64_t)slot * 0x20, 0);
        return;
    }''')
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == MII_HEAP_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MII_HEAP_SHA256:
            raise ValueError('Mii heap preparation requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8')
        entry = 'void f_70003e0ea0(Cpu *c) {'
        if text.count(entry) != 1:
            raise ValueError('Mii heap preparation boundary changed')
        # This void preparation only sizes/allocates Mii models. Constructor zeros +0x1058/+0x1060.
        text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer()) return;')
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == MODEL_PART_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MODEL_PART_SHA256:
            raise ValueError('Model part policy requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8')
        entry = 'void f_700069127c(Cpu *c) {'
        if text.count(entry) != 1:
            raise ValueError('Model part update boundary changed')
        # A model's part list (f_7000690fd8 walks count +0x40, array +0x48) held an empty entry when the next online
        # race loaded, and this update read null+0x1c (2026-11-13). Suspected absent Mii model: no-Mii mode only,
        # skipped and logged (runtime/mk8/mii/no_mii.c). Its 13 callers do not read a result.
        text = text.replace(entry, entry+'\n    if (!c->x[1] && mk8_no_mii_null_part(c->x[0])) return;')
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#include <stdint.h>\nextern int mk8_no_mii_null_part(uint64_t owner);\n'+text, encoding='utf-8')
        return target
    if src.name == UI_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != UI_SHA256:
            raise ValueError('Mii UI policy requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8')
        # Vtable +0x08/+0x10 count Mii instances; +0x20 enables their model resources.
        # Counts must also be zero: otherwise 0x3e10e8 still creates heaps for absent models.
        for address in ('70004297b4', '70004297bc', '70004297cc'):
            entry = f'void f_{address}(Cpu *c) {{'
            if text.count(entry) != 1:
                raise ValueError('Mii UI policy boundary changed')
            text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer()) { c->x[0] = 0; return; }')
        # The original readiness check unrolls 12+1 Mii slots instead of checking the counts.
        entry = 'void f_7000428b88(Cpu *c) {'
        if text.count(entry) != 1:
            raise ValueError('Mii UI readiness boundary changed')
        text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer() && LD32(c->x[0] + 0x18) == 0 && LD32(c->x[0] + 0x28) == 0) { c->x[0] = 1; return; }')
        # LAN transitions animate or configure absent models despite both counts being zero. 7000428810 and
        # 7000428838 are the online vote's twins of 70004289ac/70004289d0 (null+0x4ae written at the reveal, 2026-11-12).
        for address in ('7000428a34', '7000428880', '70004288a0', '7000428900',
                        '70004289ac', '70004289d0', '7000428a18',
                        '7000428d94', '7000428f80', '7000428fb0',
                        '7000428810', '7000428838'):
            entry = f'void f_{address}(Cpu *c) {{'
            if text.count(entry) != 1:
                raise ValueError('Mii LAN animation boundary changed')
            text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer() && LD32(c->x[0] + 0x18) == 0 && LD32(c->x[0] + 0x28) == 0) return;')
        # An absent model is not in animation state 10. The callers skip their pose update when false.
        # 70004288bc: LAN transition; 7000428720: online room's track vote (read null+0x679, 2026-11-12).
        for address in ('70004288bc', '7000428720'):
            entry = f'void f_{address}(Cpu *c) {{'
            if text.count(entry) != 1:
                raise ValueError('Mii pose query boundary changed')
            text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer() && LD32(c->x[0] + 0x18) == 0 && LD32(c->x[0] + 0x28) == 0) { c->x[0] = 0; return; }')
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('extern int mk8_no_mii_renderer(void);\n'+text, encoding='utf-8')
        return target
    if src.name == HEAP_UNIT and src.parent.name == 'main':
        raw = src.read_bytes()
        if hashlib.sha256(raw).hexdigest() != HEAP_SHA256:
            raise ValueError('SEAD heap locking requires the verified MK8 4.0.1 generated unit')
        text = raw.decode('utf-8').replace('\r\n', '\n')
        if text.count(HEAP_SELECT) != 1:
            raise ValueError('SEAD heap constructor changed')
        # Host threads require the heap lock even when the guest constructor omits it.
        text = text.replace(HEAP_SELECT, HEAP_SELECT.replace('((COND_NE(c)) ? (uint32_t)c->x[9] : (uint32_t)((uint32_t)c->x[8]))', 'c->x[9]'))
        target = output/'experimental'/src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        return target
    if src.name != UNIT or src.parent.name != 'main':
        return src
    raw = src.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('No-Mii hook requires the verified MK8 4.0.1 generated unit')
    text = raw.decode('utf-8')
    # Mii manager preparation and its periodic update, both void virtual methods.
    for address in ('700080fa44', '7000810278'):
        entry = f'void f_{address}(Cpu *c) {{'
        if text.count(entry) != 1:
            raise ValueError('Mii lifecycle boundary changed')
        hook = 'if (mk8_no_mii_renderer()) return;'
        if address == '700080fa44':
            # Loading CharInfo is independent of allocating/rendering the Mii models below.
            hook = 'if (mk8_no_mii_renderer()) { c->x[0] = LD64(c->x[0] + 0x58); f_700080e1d8(c); return; }'
        text = text.replace(entry, entry+'\n    '+hook)
    # Mii resource-list notification: disabled preparation leaves this manager absent.
    entry = 'void f_7000812fc0(Cpu *c) {'
    if text.count(entry) != 1:
        raise ValueError('Mii resource-list boundary changed')
    text = text.replace(entry, entry+'\n    if (!c->x[0] && mk8_no_mii_renderer()) return;')
    # Portrait request: with preparation skipped, the engine's worker list (+0x548) stays null and the request read
    # address 0 when an online/LAN lobby showed players (f_700080f4a4 after f_7000618098, 2026-11-07 and 2026-11-12).
    # The caller f_70000d4ffc stores the handle at +0x1538, and its release paths already treat 0 as no portrait.
    entry = 'void f_700080f4a4(Cpu *c) {'
    if text.count(entry) != 1:
        raise ValueError('Mii portrait request boundary changed')
    text = text.replace(entry, entry+'\n    if (mk8_no_mii_renderer() && !LD64(c->x[0] + 0x548)) { c->x[0] = 0; return; }')
    # Portrait to pane: reads handle+8 unchecked; the absent portrait leaves the pane as it is (result unused by callers).
    entry = 'void f_7000810c90(Cpu *c) {'
    if text.count(entry) != 1:
        raise ValueError('Mii portrait pane boundary changed')
    text = text.replace(entry, entry+'\n    if (!c->x[0] && mk8_no_mii_renderer()) return;')
    text = 'extern int mk8_no_mii_renderer(void);\n'+text
    target = output/'experimental'/src.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return target
