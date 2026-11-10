"""Build the pinned Windows runtime and link the private game library."""
import concurrent.futures
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

from native import DEFAULT_COMPILER, ROOT, TOOLS, compiler_env, compiler_path, default_workers, digest

DEPS = TOOLS / 'deps/packages/ucrt64'


def prepare_host_sources(source, output):
    runtime = output/'runtime'
    shutil.copytree(source/'runtime', runtime, dirs_exist_ok=True)
    for dependency in ('zstd', 'bcdec'):
        shutil.copytree(source/'third_party'/dependency, output/'third_party'/dependency, dirs_exist_ok=True)
    ipc = runtime/'ipc.h'
    text = ipc.read_text(encoding='utf-8')
    old = 'uint8_t data[0x200];'
    if text.count(old) != 1:
        raise ValueError('IPC response layout changed upstream')
    # Host-only structure: IPC serialization copies data explicitly, not this padding.
    ipc.write_text(text.replace(old, 'uint8_t data[0x200] __attribute__((aligned(8)));'), encoding='utf-8')
    language = runtime/'services/hid_set.c'
    text = language.read_text(encoding='utf-8')
    old = 'uint64_t *out = (uint64_t *)(uintptr_t)b->addr;'
    if text.count(old) != 1:
        raise ValueError('Language buffer implementation changed upstream')
    # The guest's output buffer may be only four-byte aligned; cpu.h defines u64u for this ABI.
    language.write_text(text.replace(old, 'u64u *out = (u64u *)(uintptr_t)b->addr;'), encoding='utf-8')
    objects = runtime/'nvn/objects.c'
    text = objects.read_text(encoding='utf-8')
    old = 'TB(uint64_t, 0x50) = A(1) ? *(uint64_t *)AP(1) : 0;'
    if text.count(old) != 1:
        raise ValueError('NVN packaged texture layout changed upstream')
    # Guest packaged layouts can start at a four-byte boundary.
    objects.write_text(text.replace(old, 'TB(uint64_t, 0x50) = A(1) ? *(u64u *)AP(1) : 0;'), encoding='utf-8')
    # Diagnostic flight recorder: establish whether queued render targets outlive their objects.
    # Diagnostic switches are read once (a GNU statement expression with its own static): getenv locks and scans
    # the environment, and these run per texture, per render target of every draw and per audio mix.
    text = objects.read_text(encoding='utf-8')
    for old, new in [
        ('    game_texture_init(t->addr, t->width, t->height, t->format);',
         '    if (({ static int mk8_on = -1; if (mk8_on < 0) mk8_on = getenv("MK8_TRACE_TEXTURES") != NULL; mk8_on; })) LOG("[mk8:tex-new] guest=%llx host=%p storage=%llx size=%ux%u format=%x lr=%llx\\n", A(0), (void *)t, t->addr, t->width, t->height, t->format, c->x[30]);\n    game_texture_init(t->addr, t->width, t->height, t->format);'),
        ('    if (t) { game_texture_final(t->addr); backend_texture_destroyed(t); free(t); }',
         '    if (({ static int mk8_on = -1; if (mk8_on < 0) mk8_on = getenv("MK8_TRACE_TEXTURES") != NULL; mk8_on; })) LOG("[mk8:tex-final] guest=%llx host=%p lr=%llx\\n", A(0), (void *)t, c->x[30]);\n    if (t) { game_texture_final(t->addr); backend_texture_destroyed(t); free(t); }')]:
        if text.count(old) != 1:
            raise ValueError('Texture lifetime diagnostic boundary changed')
        text = text.replace(old, new)
    objects.write_text(text, encoding='utf-8')
    execute = runtime/'nvn/vulkan/exec.c'
    text = execute.read_text(encoding='utf-8')
    old = '        VkFormat base = b ? b->format : vk_format(t->format);'
    if text.count(old) != 1:
        raise ValueError('Render-target layout diagnostic boundary changed')
    text = text.replace(old, '        if (({ static int mk8_on = -1; if (mk8_on < 0) mk8_on = getenv("MK8_TRACE_TEXTURES") != NULL; mk8_on; })) LOG("[mk8:rt-layout] slot=%d host=%p backend=%p\\n", i, (void *)t, (void *)b);\n'+old)
    execute.write_text(text, encoding='utf-8')
    audio = runtime/'services/audio.c'
    text = audio.read_text(encoding='utf-8')
    old = 'case 1: OUT(uint64_t, 0) = 0x1000000; resp_size(resp, 8); break;  // GetWorkBufferSize: 16 MB'
    if text.count(old) != 1:
        raise ValueError('Audio work buffer implementation changed upstream')
    # This mixer allocates all renderer state on the host and never uses transfer memory.
    # Reserve one aligned page for the SDK transfer-memory handle, not a fictitious 16 MB DSP heap.
    audio.write_text(text.replace(old, 'case 1: OUT(uint64_t, 0) = 0x1000; resp_size(resp, 8); LOG("[audren] host mixer transfer buffer: 4096 bytes\\n"); break;'), encoding='utf-8')
    text = audio.read_text(encoding='utf-8')
    old = '''    oh[10] = 0x10;  // render info: elapsed frame count
    if (o + 0x10 <= out->size) *(uint64_t *)(op + o) = elapsed;
    o += 0x10;'''
    if text.count(old) != 1:
        raise ValueError('Audio elapsed frame layout changed upstream')
    text = text.replace(old, '''    // Elapsed-frame output was added in REV5; MK8 requests REV4.
    oh[10] = (r->revision >> 24) >= '5' ? 0x10 : 0;
    if (oh[10] && o + oh[10] <= out->size) memcpy(op + o, &elapsed, sizeof elapsed);
    o += oh[10];''')
    old = '    oh[15] = o;'
    if text.count(old) != 1:
        raise ValueError('Audio update layout changed upstream')
    audio.write_text(text.replace(old, old+'\n    if (r->elapsed_frames < 2) LOG("[audren:layout] revision=%08x out=%llu written=%u pools=%u voices=%u effects=%u sinks=%u perf=%u behavior=%u render=%u\\n", r->revision, out->size, o, oh[2], oh[3], oh[5], oh[7], oh[8], oh[1], oh[10]);'), encoding='utf-8')
    text = audio.read_text(encoding='utf-8')
    old = '    LOG("[audren] mix %d used %u vol %.3f bufs %u dest %d splitter %d:",'
    if text.count(old) != 1:
        raise ValueError('Audio mix logging changed upstream')
    audio.write_text(text.replace(old, '    if (r->elapsed_frames >= 2 && !({ static int mk8_on = -1; if (mk8_on < 0) mk8_on = getenv("LARECOMP_AUDIO_LOG") != NULL; mk8_on; })) return;\n'+old), encoding='utf-8')
    dispatch = runtime/'dispatch.c'
    text = dispatch.read_text(encoding='utf-8')
    old = '    Cpu *c = kernel_current_cpu();'
    if text.count(old) != 1:
        raise ValueError('Fatal diagnostics changed upstream')
    dispatch.write_text(text.replace(old, old+'\n    extern void mk8_heap_diagnostic(Cpu *c);\n    mk8_heap_diagnostic(c);'), encoding='utf-8')
    fonts = runtime/'hle/pl.c'
    text = fonts.read_text(encoding='utf-8')
    symbol = '_ZN2nn2pl13GetSharedFontEPiPNS0_14SharedFontDataEiNS_8settings12LanguageCodeE'
    if symbol in text:
        raise ValueError('Shared font HLE now exists upstream; review MK8 adapter')
    # MK8's font rasterizer rejected the Yu Gothic TTC fallback in the boot test.
    old = '"C:/Windows/Fonts/YuGothR.ttc",'
    if text.count(old) != 1:
        raise ValueError('Font fallback list changed upstream')
    text = text.replace(old, '"C:/Windows/Fonts/arial.ttf", '+old)
    fonts.write_text(text+'\n'+(ROOT/'runtime/mk8/services/shared_font.inc').read_text(encoding='utf-8'), encoding='utf-8')
    touch = runtime/'hle/touch.c'
    text = touch.read_text(encoding='utf-8')
    symbol = '_ZN2nn3hid20GetTouchScreenStatesILm16EEEiPNS0_16TouchScreenStateIXT_EEEi'
    if symbol in text:
        raise ValueError('Plural touch HLE now exists upstream; review MK8 adapter')
    touch.write_text(text+'\n'+(ROOT/'runtime/mk8/services/touch_states.inc').read_text(encoding='utf-8'), encoding='utf-8')
    input_source = runtime/'hle/input.c'
    text = input_source.read_text(encoding='utf-8')
    old = 'static void host_test_frame(uint64_t frame) {'
    if text.count(old) != 1:
        raise ValueError('Diagnostic frame hook changed upstream')
    text = text.replace(old, (ROOT/'runtime/mk8/graphics/frame_capture.inc').read_text(encoding='utf-8')+'\n'+old+'\n    mk8_capture_frame(frame);')
    input_source.write_text(text, encoding='utf-8')
    mii = runtime/'services/mii.c'
    text = mii.read_text(encoding='utf-8')
    old = 'static bool one_mii(void) { return net_backend() == NET_BACKEND_OCW; }'
    if text.count(old) != 1:
        raise ValueError('Mii database policy changed upstream')
    text = text.replace(old, (ROOT/'runtime/mk8/mii/local_mii.inc').read_text(encoding='utf-8')+'\nstatic bool one_mii(void) { return mk8_profile_charinfo(NULL) || net_backend() == NET_BACKEND_OCW; }')
    old = '            mii_default_charinfo(p, getenv("RECOMP_USER_NAME"));'
    if text.count(old) != 1:
        raise ValueError('Mii database Get implementation changed upstream')
    text = text.replace(old, '            if (!mk8_profile_charinfo(p)) mii_default_charinfo(p, getenv("RECOMP_USER_NAME"));\n            else LOG("[mk8:mii] persistent local profile returned by Get%u\\n", req->cmd);')
    old = 'static void database(Service *s, IpcReq *req, IpcResp *resp) {'
    if text.count(old) != 1:
        raise ValueError('Mii database diagnostic boundary changed upstream')
    text = text.replace(old, old+'\n    if (getenv("MK8_TRACE_MII")) LOG("[mk8:mii] cmd=%u local-profile=%d\\n", req->cmd, mk8_profile_charinfo(NULL));')
    old = 'OUT(uint32_t, 0) = one_mii();'
    if text.count(old) != 1:
        raise ValueError('Mii count boundary changed upstream')
    text = text.replace(old, 'OUT(uint32_t, 0) = mk8_mii_count(IN(uint32_t, 0), one_mii());')
    start = text.index('        int32_t n = 0;', text.index('    case 3: case 4:'))
    end = text.index('        OUT(int32_t, 0) = n;', start)
    text = text[:start]+'''        int32_t n = b ? mk8_mii_get((uint8_t *)(uintptr_t)b->addr, b->size, want, IN(uint32_t, 0), one_mii()) : 0;
        if (getenv("MK8_TRACE_MII")) LOG("[mk8:mii] Get%u sources=%u count=%d\\n", req->cmd, IN(uint32_t, 0), n);
'''+text[end:]
    old = '    case 0: case 1: case 20:'
    if text.count(old) != 1:
        raise ValueError('Mii IsUpdated boundary changed upstream')
    text = text.replace(old, '    case 0: OUT(uint8_t, 0) = mk8_mii_updated; mk8_mii_updated = false; resp_size(resp, 1); break;\n    case 1: case 20:')
    mii.write_text(text+'\n'+(ROOT/'runtime/mk8/mii/mii_editor.inc').read_text(encoding='utf-8'), encoding='utf-8')
    from mii_applet import adapt as adapt_mii_applet
    am = runtime/'services/am.c'
    am.write_text(adapt_mii_applet(am.read_text(encoding='utf-8')), encoding='utf-8')
    movie = runtime/'hle/movie.c'
    text = movie.read_text(encoding='utf-8')
    constructor = 'HLE_FUNC("_ZN5movie9ExtractorC1ENS_13ContainerTypeE", movie_extractor_ctor) {'
    slots = 'HostFn f[16] = {ex_dtor_l, ex_dtor_l, movie_log_slot, movie_log_slot, ex_get_track_count_l,'
    if text.count(constructor) != 1 or text.count(slots) != 1:
        raise ValueError('Movie extractor ABI changed upstream')
    text = text.replace(constructor, (ROOT/'runtime/mk8/services/movie_file_source.inc').read_text(encoding='utf-8')+'\n'+constructor)
    text = text.replace(slots, slots.replace('movie_log_slot, movie_log_slot', 'mk8_ex_set_file_source, movie_log_slot'))
    for old, trace in [
        ('    *(int32_t *)(uintptr_t)c->x[1] = e->fmt ? e->ntracks : 0;', '    if (getenv("MK8_TRACE_MOVIE")) LOG("[mk8:movie] tracks=%d\\n", e->fmt ? e->ntracks : 0);'),
        ('    AVCodecParameters *p = s->codecpar;', '    if (getenv("MK8_TRACE_MOVIE")) LOG("[mk8:movie] config track=%d type=%d codec=%d\\n", i, s->codecpar->codec_type, s->codecpar->codec_id);'),
        ('    const char *k = (const char *)(uintptr_t)c->x[1];', '    if (getenv("MK8_TRACE_MOVIE")) LOG("[mk8:movie] metadata key=%s\\n", k);'),
    ]:
        if old not in text:
            raise ValueError('Movie diagnostics boundary changed upstream')
        text = text.replace(old, old+'\n'+trace)
    for name in ('dec_stop', 'dec_signal'):
        pattern = rf'static void {name}\([^\n]+\) \{{\n.*?\n\}}'
        replacement = (ROOT/'runtime/mk8/services/movie_events.inc').read_text(encoding='utf-8') if name == 'dec_signal' else ''
        text, count = re.subn(pattern, lambda match: replacement, text, flags=re.S)
        if count != 1:
            raise ValueError(f'Movie event boundary changed: {name}')
    old = '    dec_signal(c, d, 0);  // input may be accepted again\n'
    if text.count(old) != 1:
        raise ValueError('Movie release boundary changed')
    text = text.replace(old, '')
    boundary = 'static void dec_release_output(Cpu *c) {'
    start = text.index(boundary)
    pos = text.index('    pthread_mutex_unlock(&d->lock);', start) + len('    pthread_mutex_unlock(&d->lock);')
    text = text[:pos]+'\n    dec_signal(c, d, 0);  // signal only after the input backpressure is relieved'+text[pos:]
    movie.write_text(text+'\n'+(ROOT/'runtime/mk8/services/movie_compat.inc').read_text(encoding='utf-8'), encoding='utf-8')
    translator = runtime/'nvn/maxwell/translate.c'
    text = translator.read_text(encoding='utf-8')
    boundary = 'static void emit_tld4(Ctx *c, uint64_t w) {'
    opcode = '    } else if (OPM(0xc800000000000000ull, 0xfc00000000000000ull)) {  // TLD4'
    if text.count(boundary) != 1 or text.count(opcode) != 1 or 'emit_tld4s' in text:
        raise ValueError('TLD4S translator boundary changed')
    text = text.replace(boundary, (ROOT/'runtime/mk8/graphics/maxwell_gather.inc').read_text(encoding='utf-8')+'\n'+boundary)
    text = text.replace(opcode, '    } else if (OPM(0xdf00000000000000ull, 0xffc0000000000000ull)) {  // TLD4S F32\n        mk8_emit_tld4s(c, w);\n'+opcode)
    translator.write_text(text, encoding='utf-8')
    from texture_lifetime import prepare as prepare_texture_lifetime
    prepare_texture_lifetime(runtime)
    from gpu_sync import prepare as prepare_gpu_sync
    prepare_gpu_sync(runtime)
    from lan import prepare as prepare_lan
    prepare_lan(runtime)
    from compute_flush import prepare as prepare_compute_flush
    prepare_compute_flush(runtime)
    from hot_paths import prepare as prepare_hot_paths
    prepare_hot_paths(runtime)
    from occlusion import prepare as prepare_occlusion
    prepare_occlusion(runtime)
    from shader_ahead import prepare as prepare_shader_ahead
    prepare_shader_ahead(runtime)
    from texture_init import prepare as prepare_texture_init
    prepare_texture_init(runtime)
    from texture_pool import prepare as prepare_texture_pool
    prepare_texture_pool(runtime)
    from bc_render import prepare as prepare_bc_render
    prepare_bc_render(runtime)
    from split_screen import prepare as prepare_split_screen
    prepare_split_screen(runtime)
    from online_notice import prepare as prepare_online_notice
    prepare_online_notice(runtime)
    from movie_output import prepare as prepare_movie_output
    prepare_movie_output(runtime)
    from sampler_inline import prepare as prepare_sampler_inline
    prepare_sampler_inline(runtime)
    from submit_ring import prepare as prepare_submit_ring
    prepare_submit_ring(runtime)
    from shader_budget import prepare as prepare_shader_budget
    prepare_shader_budget(runtime)
    from async_submit import prepare as prepare_async_submit
    prepare_async_submit(runtime)
    from texture_release_thread import prepare as prepare_texture_release_thread
    prepare_texture_release_thread(runtime)
    from pool_release import prepare as prepare_pool_release
    prepare_pool_release(runtime)
    from pipeline_cache_save import prepare as prepare_pipeline_cache_save
    prepare_pipeline_cache_save(runtime)
    from ipc_core_release import prepare as prepare_ipc_core_release
    prepare_ipc_core_release(runtime)
    from save_file_close import prepare as prepare_save_file_close
    prepare_save_file_close(runtime)
    from vram_trace import prepare as prepare_vram_trace
    prepare_vram_trace(runtime)
    device = runtime/'nvn/vulkan/device.c'
    text = device.read_text(encoding='utf-8')
    boundary = '    VK_CHECK(vmaCreateAllocator(&ai, &g_vk.vma));'
    if text.count(boundary) != 1:
        raise ValueError('VMA allocator initialization boundary changed')
    # Smaller reservation steps for track transitions, without evicting GPU-owned contents.
    device.write_text(text.replace(boundary, '    ai.preferredLargeHeapBlockSize = 128ull << 20;\n' + boundary), encoding='utf-8')
    menu = runtime/'ui/menu.mm'
    text = menu.read_text(encoding='utf-8')
    boundary = '        tabs_body(scale, true, NULL);'
    if text.count(boundary) != 1:
        raise ValueError('Runtime room menu boundary changed')
    text = text.replace(boundary, '        if (mk8_room_menu_available() && ImGui::Button("Friend rooms")) mk8_open_room_menu();\n' + boundary)
    menu.write_text('extern "C" bool mk8_room_menu_available(void);\nextern "C" void mk8_open_room_menu(void);\n' + text, encoding='utf-8')
    # MK8 keyboard defaults instead of SMM2's: arrows steer, X/Z accelerate/brake, Space item, Left Shift drift.
    # Must match MK8_KEYS in apps/launcher/src/game_settings.rs (the launcher's Reset).
    bindings = runtime/'hle/bindings.c'
    text = bindings.read_text(encoding='utf-8')
    smm2_keys = ('    SDL_SCANCODE_K, SDL_SCANCODE_J, SDL_SCANCODE_L, SDL_SCANCODE_I, SDL_SCANCODE_F, SDL_SCANCODE_KP_0, \\\n'
                 '    SDL_SCANCODE_Q, SDL_SCANCODE_E, SDL_SCANCODE_1, SDL_SCANCODE_3, SDL_SCANCODE_RETURN, SDL_SCANCODE_BACKSPACE, \\\n'
                 '    SDL_SCANCODE_LEFT, SDL_SCANCODE_UP, SDL_SCANCODE_RIGHT, SDL_SCANCODE_DOWN, \\\n'
                 '    SDL_SCANCODE_W, SDL_SCANCODE_S, SDL_SCANCODE_A, SDL_SCANCODE_D, SDL_SCANCODE_KP_8, SDL_SCANCODE_KP_5, \\\n')
    mk8_keys = ('    SDL_SCANCODE_X, SDL_SCANCODE_Z, SDL_SCANCODE_C, SDL_SCANCODE_V, SDL_SCANCODE_F, SDL_SCANCODE_KP_0, \\\n'
                '    SDL_SCANCODE_SPACE, SDL_SCANCODE_LSHIFT, SDL_SCANCODE_Q, SDL_SCANCODE_E, SDL_SCANCODE_RETURN, SDL_SCANCODE_BACKSPACE, \\\n'
                '    SDL_SCANCODE_J, SDL_SCANCODE_I, SDL_SCANCODE_L, SDL_SCANCODE_K, \\\n'
                '    SDL_SCANCODE_UP, SDL_SCANCODE_DOWN, SDL_SCANCODE_LEFT, SDL_SCANCODE_RIGHT, SDL_SCANCODE_KP_8, SDL_SCANCODE_KP_5, \\\n')
    if text.count(smm2_keys) != 1:
        raise ValueError('Runtime default key bindings changed')
    bindings.write_text(text.replace(smm2_keys, mk8_keys), encoding='utf-8')
    # OpenSaveDataFileSystem(u8 space, SaveDataAttribute at 0x8): the type is attribute+0x20; upstream reads +0x18
    # (system_save_data_id, 0 for games), so every save MK8 mounts shared save_0 and the game's own save was
    # never found again (each boot made a new one). The account save keeps save_0, where existing saves live.
    fs = runtime/'services/fs.c'
    text = fs.read_text(encoding='utf-8')
    mount = ('        uint8_t type = IN(uint8_t, 0x8 + 0x18);  // attribute.type\n'
             '        char sub[64];\n'
             '        snprintf(sub, sizeof sub, "save_%u", type);\n')
    if text.count(mount) != 1:
        raise ValueError('Save data mount boundary changed')
    fs.write_text(text.replace(mount, '''        uint8_t type = IN(uint8_t, 0x8 + 0x20);  // attribute.type (MK8: toolchain/host.py)
        char sub[64];
        if (type == 1) snprintf(sub, sizeof sub, "save_0");  // account save: where existing saves are
        else snprintf(sub, sizeof sub, "save_type%u", type);
        LOG("[fs] OpenSaveDataFileSystem cmd %u space %u type %u\\n", req->cmd, IN(uint8_t, 0), type);
'''), encoding='utf-8')
    kernel = runtime/'kernel.c'
    text = kernel.read_text(encoding='utf-8')
    stack = '    pthread_attr_setstacksize(&attr, 256ull << 20);  // guest recursion runs on the host stack'
    if text.count(stack) != 1:
        raise ValueError('Guest host-stack boundary changed')
    # Winpthreads passes an explicit size as a commit request. The PE default reserves
    # 256 MiB and commits on demand instead, retaining room for recompiled recursion.
    text = text.replace(stack, '    // Use the executable default stack reserve; do not commit 256 MiB per thread.')
    map_copy = '    case 0x04:  // MapMemory(dst, src, size): src is inaccessible while mapped, so copying is equivalent\n'
    if text.count(map_copy) != 1:
        raise ValueError('MapMemory boundary changed')
    text = text.replace(map_copy, map_copy + '''        if (!x[2] || x[0] < GUEST_LO || x[0] >= GUEST_HI || x[2] > GUEST_HI - x[0] ||
            x[1] < GUEST_LO || x[1] >= GUEST_HI || x[2] > GUEST_HI - x[1] ||
            (x[0] < x[1] + x[2] && x[1] < x[0] + x[2])) {
            x[0] = R_INVALID_ADDRESS;
            break;
        }
        // Back the destination before memcpy crosses into an uncommitted Windows page.
        plat_commit(x[0], x[2]);
''')
    boundary = '            uint64_t waited = (plat_now_ns() - t0) / 1000000;'
    if text.count(boundary) != 1:
        raise ValueError('Kernel stall diagnostic boundary changed')
    text = text.replace(boundary, boundary+'''\n            if (waited >= 2000 && waited < 2300 && getenv("MK8_TRACE_STALL")) {
                extern void kernel_dump_threads(void);
                kernel_dump_threads();
            }''')
    kernel.write_text(text, encoding='utf-8')
    windows = runtime/'platform/windows.c'
    text = windows.read_text(encoding='utf-8')
    boundary = 'static LONG CALLBACK vectored_handler(EXCEPTION_POINTERS *ep) {\n    EXCEPTION_RECORD *er = ep->ExceptionRecord;\n'
    if text.count(boundary) != 1:
        raise ValueError('Windows exception handler boundary changed')
    text = text.replace(boundary, boundary + '''    // The legacy debugger thread-name notification can escape SDL's fallback on Windows.
    if (er->ExceptionCode == 0x406D1388 && !(er->ExceptionFlags & EXCEPTION_NONCONTINUABLE) &&
        er->NumberParameters >= 2 && (DWORD)er->ExceptionInformation[0] == 0x1000)
        return EXCEPTION_CONTINUE_EXECUTION;
''')
    windows.write_text(text, encoding='utf-8')
    return runtime


def run(args, log, env):
    result = subprocess.run([str(a) for a in args], capture_output=True, env=env)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError(f'{log.name}:\n' + result.stderr.decode('utf-8', 'replace')[-6000:])


def build_host(source, output, workers=None):
    source, output = map(compiler_path, (source, output))
    from storage import require_space
    require_space(output)
    output.mkdir(parents=True, exist_ok=True)
    runtime = prepare_host_sources(source, output)
    for header in ['SDL3/SDL.h', 'vulkan/vulkan.h', 'shaderc/shaderc.h', 'libavcodec/avcodec.h']:
        if not (DEPS/'include'/header).is_file():
            raise ValueError('Dependências do runtime ausentes: execute scripts/setup-runtime-deps.py')
    env = dict(compiler_env(), PYTHONUTF8='1')
    inc = output/'inc'; inc.mkdir(exist_ok=True)
    # The upstream Makefile replaces non-ASCII bytes in this legacy shader comment text.
    hlsl = (source/'third_party/smaa/SMAA.hlsl').read_bytes().decode('ascii', errors='replace').replace('\ufffd', '?')
    (inc/'smaa_hlsl.inc').write_text('\n'.join(json.dumps(line+'\n', ensure_ascii=True) for line in hlsl.splitlines()), encoding='utf-8')
    shim = output/'vk_shim.c'
    run([sys.executable, source/'tools/gen_vk_shim.py', DEPS/'include', source/'runtime/platform/vulkan-1.exports', shim], output/'vk-shim.log', env)
    # Export only the shared runtime ABI used by the generated game code.
    exported = ['rt_dispatch', 'rt_svc', 'rt_brk', 'rt_udf', 'rt_ticks', 'fnprof_hit', 'g_fnprof DATA', 'mk8_no_mii_renderer', 'mk8_setjmp_arm']
    exports = output/'exports.def'
    exports.write_text('LIBRARY mk8drecomp.exe\nEXPORTS\n'+'\n'.join(exported)+'\n', encoding='utf-8')
    export_source = output/'mk8_exports.c'
    directives = ''.join(' -export:'+name.replace(' DATA', ',data') for name in exported)
    export_source.write_text('__asm__(".section .drectve\\n.ascii \\"'+directives+'\\"\\n.text\\n");\n', encoding='utf-8')
    common = ['-target', 'x86_64-windows-gnu', '-mcpu=x86_64_v2', '-O2', '-fno-strict-aliasing',
              '-D_FILE_OFFSET_BITS=64', '-D_POSIX_THREAD_SAFE_FUNCTIONS', '-D_CRT_RAND_S',
              '-DWIN32_LEAN_AND_MEAN', '-DNOMINMAX', '-DGAME_ID="mk8d"',
              '-DGAME_TITLE="Mario Kart 8 Deluxe - experimental"', '-DGAME_TITLE_ID=0x0100152000022000ull',
              '-DGAME_DISPLAY_VERSION="4.0.1"', '-DGAME_BUNDLE_ID="org.mk8online.mk8d"',
              '-DMBEDTLS_USER_CONFIG_FILE="mbedtls_user_config.h"',
              # Only __FILE__ text (assertion messages) changes: the code is the same, and a prebuilt runtime carries
              # no folder of the machine that built it.
              *('-ffile-prefix-map='+str(path)+'='+name for path, name in
                ((ROOT, 'mk8-online'), (DEPS, 'deps'), (source, 'runtime'), (output, 'host')))]
    for p in [runtime, runtime/'nvn', runtime/'net', DEPS/'include',
              source/'third_party/mbedtls/include', source/'third_party/imgui', source/'third_party/imgui/backends',
              source/'third_party/smaa/Textures', inc]:
        common.append('-I'+str(p))
    sources = []
    for pattern in ['runtime/*.c', 'runtime/hle/*.c', 'runtime/services/*.c', 'runtime/nvn/*.c',
                    'runtime/nvn/maxwell/*.c', 'runtime/net/*.c', 'runtime/nvn/vulkan/*.c',
                    'runtime/nvn/vulkan/*.cpp', 'runtime/ui/*.mm', 'third_party/mbedtls/library/*.c', 'games/mk8d/*.c']:
        sources.extend(sorted(source.glob(pattern)))
    sources = [p for p in sources if p.name not in ('iosbuild.c', 'nextendo_ios.mm', 'launcher.mm')]
    sources.extend(sorted((ROOT/'runtime/mk8').rglob('*.c')))
    sources += [source/'runtime/platform/windows.c', export_source]
    sources += [source/'third_party/imgui'/name for name in ['imgui.cpp', 'imgui_draw.cpp', 'imgui_tables.cpp', 'imgui_widgets.cpp', 'backends/imgui_impl_sdl3.cpp', 'backends/imgui_impl_vulkan.cpp']]
    sources.append(shim)
    sources = [runtime/p.relative_to(source/'runtime') if p.is_relative_to(source/'runtime') else p for p in sources]
    headers = {str(p.relative_to(source)): digest(p) for folder in ['runtime', 'third_party', 'games/mk8d'] for p in (source/folder).rglob('*.h')}
    headers.update({'prepared/'+str(p.relative_to(runtime)): digest(p) for p in runtime.rglob('*.h')})
    dep_headers = {str(p.relative_to(DEPS)): digest(p) for p in (DEPS/'include').rglob('*.h')}
    environment = hashlib.sha256(json.dumps({'headers': headers, 'deps': dep_headers, 'smaa': digest(inc/'smaa_hlsl.inc'), 'compiler': digest(DEFAULT_COMPILER), 'flags': common}, sort_keys=True).encode()).hexdigest()

    def relative_of(src):
        return Path('runtime')/src.relative_to(runtime) if src.is_relative_to(runtime) else src.relative_to(source) if src.is_relative_to(source) else Path(src.name)

    key = host_key({'headers': headers, 'deps': dep_headers, 'smaa': digest(inc/'smaa_hlsl.inc')}, common, sources, source, output)
    prebuilt = use_prebuilt(key, output)
    if prebuilt:
        copy_runtime_dlls(output)
        print('Runtime Windows pré-compilado do pacote: chave e hashes conferidos, sem recompilar.', flush=True)
        return prebuilt

    def compile_one(src):
        relative = relative_of(src)
        obj = output/'obj'/relative.with_suffix('.o'); obj.parent.mkdir(parents=True, exist_ok=True)
        stamp = obj.with_suffix('.json')
        key = {'environment': environment, 'source': digest(src)}
        if obj.exists() and stamp.exists():
            saved = json.loads(stamp.read_text(encoding='utf-8'))
            if saved.get('input') == key and saved.get('sha256') == digest(obj):
                return obj
        cpp = src.suffix in ('.mm', '.cpp')
        flags = ['-x', 'c++', '-std=c++17'] if cpp else []
        pending = obj.with_suffix('.pending.o')
        require_space(output)
        run([DEFAULT_COMPILER, 'c++' if cpp else 'cc', *common, *flags, '-c', src, '-o', pending], obj.with_suffix('.log'), env)
        pending.replace(obj)
        stamp.write_text(json.dumps({'input': key, 'sha256': digest(obj)}), encoding='utf-8')
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers or default_workers()) as pool:
        objects = []
        for i, obj in enumerate(pool.map(compile_one, sources), 1):
            objects.append(obj)
            print(f'Runtime Windows: {i}/{len(sources)} unidades', flush=True)
    rsp = output/'host.rsp'
    rsp.write_text('\n'.join('"'+str(p).replace('\\','/')+'"' for p in objects), encoding='utf-8')
    exe = output/'mk8drecomp.exe'
    run([DEFAULT_COMPILER, 'c++', '-target', 'x86_64-windows-gnu', '@'+str(rsp), '-o', exe,
         '-L'+str(DEPS/'lib'), '-Wl,--stack,268435456', '-Wl,--export-all-symbols',
         *(DEPS/'lib'/('lib'+lib+'.dll.a') for lib in ['SDL3', 'avformat', 'avcodec', 'avutil', 'swscale', 'shaderc_shared']),
         *('-l'+lib for lib in ['winmm', 'ole32', 'shell32', 'comdlg32', 'uuid', 'ws2_32', 'bcrypt'])], output/'host-link.log', env)
    run([DEFAULT_COMPILER, 'dlltool', '-m', 'i386:x86-64', '-d', exports, '-l', output/'mk8drecomp.dll.a'], output/'exports.log', env)
    copy_runtime_dlls(output)
    (output/'host-key.json').write_text(json.dumps({'key': key, 'files': {name: digest(output/name) for name in HOST_FILES}}, indent=2), encoding='utf-8')
    print('Executável do runtime ligado; gameplay ainda não verificado.', flush=True)
    return exe


HOST_FILES = ('mk8drecomp.exe', 'mk8drecomp.dll.a')
PREBUILT = ROOT/'prebuilt/host'


PREBUILT_FFMPEG = ROOT/'prebuilt/ffmpeg'
FFMPEG_PATTERNS = ['avcodec-*.dll', 'avformat-*.dll', 'avutil-*.dll', 'swscale-*.dll']


def ffmpeg_dlls():
    """The minimal FFmpeg from the setup (scripts/build-ffmpeg.py) when its manifest and every hash match, else the
    pinned MSYS2 build. Same version and file names either way; the runtime links the same import libraries."""
    manifest = PREBUILT_FFMPEG/'manifest.json'
    try:
        files = json.loads(manifest.read_text(encoding='utf-8'))['files'] if manifest.is_file() else {}
    except (OSError, ValueError, KeyError):
        files = {}
    names = {pattern: [name for name in files if Path(name).match(pattern)] for pattern in FFMPEG_PATTERNS}
    if files and all(len(found) == 1 for found in names.values()) and all(
            (PREBUILT_FFMPEG/name).is_file() and digest(PREBUILT_FFMPEG/name) == sha for name, sha in files.items()):
        return [PREBUILT_FFMPEG/found[0] for found in names.values()]
    return [dll for pattern in FFMPEG_PATTERNS for dll in sorted((DEPS/'bin').glob(pattern))]


def copy_runtime_dlls(output):
    ffmpeg = ffmpeg_dlls()
    if len(ffmpeg) != len(FFMPEG_PATTERNS):
        raise ValueError('FFmpeg ausente: execute de novo o passo Ferramentas (scripts/setup-runtime-deps.py)')
    for dll in [*(dll for pattern in ['SDL3.dll', 'libshaderc_shared.dll', 'vulkan-1.dll'] for dll in (DEPS/'bin').glob(pattern)),
                *ffmpeg]:
        target = output/dll.name
        if not target.is_file() or digest(target) != digest(dll):
            shutil.copy2(dll, target)


def host_key(inputs, flags, sources, source, output):
    """What the runtime executable is made from, without the folders it was built in: the header, dependency and
    compiler hashes, the flags with their folders as names, every source by relative path and hash,
    and the link inputs."""
    def portable(text):
        for path, name in ((output, '<host>'), (source, '<runtime>'), (DEPS, '<deps>'), (ROOT, '<mk8-online>')):
            text = text.replace(str(path), name)
        return text
    libraries = {lib: digest(DEPS/'lib'/('lib'+lib+'.dll.a')) for lib in ['SDL3', 'avformat', 'avcodec', 'avutil', 'swscale', 'shaderc_shared']}
    return hashlib.sha256(json.dumps({'inputs': inputs, 'flags': [portable(f) for f in flags],
                                      'sources': {portable(str(src)): digest(src) for src in sources},
                                      'libraries': libraries, 'compiler': digest(DEFAULT_COMPILER)},
                                     sort_keys=True).encode()).hexdigest()


def use_prebuilt(key, output):
    """Copies the packaged runtime executable when it was built from exactly these inputs; otherwise None (compiled
    here, as before). MK8_HOST_FROM_SOURCE=1 always compiles (used to make the prebuilt one)."""
    manifest = PREBUILT/'manifest.json'
    if os.environ.get('MK8_HOST_FROM_SOURCE') == '1' or not manifest.is_file():
        return None
    try:
        recorded = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if recorded.get('key') != key or sorted(recorded.get('files', {})) != sorted(HOST_FILES):
        return None
    for name, expected in recorded['files'].items():
        if not (PREBUILT/name).is_file() or digest(PREBUILT/name) != expected:
            return None
    for name in HOST_FILES:
        pending = output/(name+'.pending')
        shutil.copyfile(PREBUILT/name, pending)
        if digest(pending) != recorded['files'][name]:
            raise ValueError('Runtime pré-compilado alterado durante a cópia: '+name)
        os.replace(pending, output/name)
    (output/'host-key.json').write_text(json.dumps(recorded, indent=2), encoding='utf-8')
    return output/'mk8drecomp.exe'


def prepare_dll_link(output):
    """Populate Zig's shared-library CRT cache while the guest objects are still compiling."""
    output.mkdir(parents=True, exist_ok=True)
    source = output/'probe.c'
    source.write_text('int mk8_link_probe(void) { return 0; }\n', encoding='utf-8')
    run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-shared', '-s',
         '-o', output/'probe.dll', source], output/'probe.log', compiler_env())
    print('Ligação de DLL do Zig pronta.', flush=True)


def link_game(archive, output, source=None, modules=None):
    output = compiler_path(output)
    env = compiler_env()
    dll = output/'mk8dgame.dll'
    source = compiler_path(source)
    objects = compiler_path(archive).parent
    from storage import GIB, require_space
    estimated_dll_bytes = sum(p.stat().st_size for module in modules for p in (objects/module).glob('*.o'))
    require_space(output, 4 * GIB + estimated_dll_bytes)
    diagnostics = output/'guest-diagnostics.o'
    run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-fno-sanitize=all', '-c', ROOT/'runtime/mk8/support/ubsan_report.c', '-o', diagnostics], output/'guest-diagnostics.log', env)
    # A diagnostic MK8 image exceeds PE32+'s 2 GiB limit. Keep each original module in its own DLL.
    def link_module(module):
        if not module.isidentifier():
            raise ValueError('Invalid module name')
        name = 'mk8d-code-'+module
        symbols = [module+'_fn_addrs', module+'_fn_ptrs', module+'_fn_count']
        export_source = output/(name+'-exports.c')
        directives = ''.join(' -export:'+symbol+',data' for symbol in symbols)
        export_source.write_text('__asm__(".section .drectve\\n.ascii \\"'+directives+'\\"\\n.text\\n");\n', encoding='utf-8')
        export_obj = output/(name+'-exports.o')
        run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-c', export_source, '-o', export_obj], output/(name+'-exports.log'), env)
        files = sorted((objects/module).glob('*.o'))
        if not files:
            raise ValueError('Missing native objects: '+module)
        extra_imports = []
        # main's code plus constant pools is still >2 GiB in -O0. Two mutually importing
        # code DLLs keep direct generated calls intact; the module DLL owns its tables.
        if module == 'main' and sum(p.stat().st_size for p in files) > 2_000_000_000:
            chunks = [p for p in files if p.stem != module+'_table']
            halves = [chunks[:len(chunks)//2], chunks[len(chunks)//2:]]
            parts = []
            for index, group in enumerate(halves):
                part = name+'-part'+str(index)
                exported = []
                for obj in group:
                    c = source.parent/'generated'/module/(obj.stem+'.c')
                    exported += re.findall(r'^void (f_[0-9a-f]+)\(Cpu \*c\) \{', c.read_text(encoding='utf-8'), re.M)
                if not exported or len(exported) > 65535:
                    raise ValueError('Invalid code export count: '+part)
                definition = output/(part+'.def')
                definition.write_text('LIBRARY '+part+'.dll\nEXPORTS\n'+'\n'.join(exported)+'\n', encoding='utf-8')
                library = output/(part+'.dll.a')
                run([DEFAULT_COMPILER, 'dlltool', '-m', 'i386:x86-64', '-d', definition, '-l', library], output/(part+'-import.log'), env)
                parts.append((part, group, exported, library))
            for part, group, exported, library in parts:
                directive_source = output/(part+'-exports.c')
                directives = ''.join(' -export:'+s for s in exported)
                directive_source.write_text('__asm__(".section .drectve\\n.ascii \\"'+directives+'\\"\\n.text\\n");\n', encoding='utf-8')
                directive_obj = output/(part+'-exports.o')
                run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-c', directive_source, '-o', directive_obj], output/(part+'-exports.log'), env)
                part_rsp = output/(part+'.rsp')
                part_rsp.write_text('\n'.join('"'+str(p).replace('\\','/')+'"' for p in group), encoding='utf-8')
                print('Ligando '+part+'...', flush=True)
                run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-shared', '-s', '-o', output/(part+'.dll'),
                     '@'+str(part_rsp), directive_obj, diagnostics, output/'mk8drecomp.dll.a',
                     *(other[3] for other in parts if other[0] != part)], output/(part+'-link.log'), env)
                extra_imports.append(library)
            files = [objects/module/(module+'_table.o')]
        rsp = output/(name+'.rsp')
        rsp.write_text('\n'.join('"'+str(p).replace('\\','/')+'"' for p in files), encoding='utf-8')
        print('Ligando módulo '+module+'...', flush=True)
        run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-shared', '-s', '-o', output/(name+'.dll'),
             '@'+str(rsp), export_obj, diagnostics, output/'mk8drecomp.dll.a', *extra_imports], output/(name+'-link.log'), env)
        definition = output/(name+'.def')
        definition.write_text('LIBRARY '+name+'.dll\nEXPORTS\n'+'\n'.join(s+' DATA' for s in symbols)+'\n', encoding='utf-8')
        library = output/(name+'.dll.a')
        run([DEFAULT_COMPILER, 'dlltool', '-m', 'i386:x86-64', '-d', definition, '-l', library], output/(name+'-import.log'), env)
        return library

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(modules), default_workers())) as pool:
        imports = list(pool.map(link_module, modules))
    entry = output/'mk8d-entry.c'
    entry.write_text('''#include "gamelib.h"
#define DECLARE(name, base) extern __declspec(dllimport) const uint64_t name##_fn_addrs[], name##_fn_count; extern __declspec(dllimport) const GuestFn name##_fn_ptrs[];
RECOMP_MODULES(DECLARE)
#undef DECLARE
GAMELIB_EXPORT const GameLibTables *recomp_game_tables(void) {
    static GameLibModule modules[RECOMP_MODULE_COUNT];
    static const GameLibTables tables = {GAMELIB_ABI, GAMELIB_ID, RECOMP_MODULE_COUNT, modules};
    unsigned i = 0;
#define ASSIGN(name, base) modules[i++] = (GameLibModule){#name, base, name##_fn_addrs, name##_fn_ptrs, &name##_fn_count};
    RECOMP_MODULES(ASSIGN)
#undef ASSIGN
    return &tables;
}
''', encoding='utf-8')
    run([DEFAULT_COMPILER, 'cc', '-target', 'x86_64-windows-gnu', '-shared', '-s', '-I'+str(source/'runtime'),
         '-o', dll, entry, *imports], output/'game-link.log', env)
    (output/'mk8dgame.dll.id').write_text('dev x86-64-v2\n', encoding='utf-8')
    return dll


if __name__ == '__main__':
    receipt = json.loads((ROOT/'local/generation.json').read_text(encoding='utf-8'))
    source = compiler_path(receipt['source'])
    build_host(source, source.parent/'host-debug')
