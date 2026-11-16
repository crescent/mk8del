"""Apply bounded MK8 adaptations to a private copy of the pinned runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re

ROOT = Path(__file__).resolve().parents[1]


def replace(source, relative, old, new):
    path = source / relative
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise ValueError(f"Upstream drift: {relative}, expected one replacement")
    path.write_text(text.replace(old, new), encoding="utf-8")


def prepare(upstream, output, profile_path=None):
    manifest = json.loads((ROOT / "upstream/manifests/smm2recomp.json").read_text())
    for relative, expected in manifest["files"].items():
        path = upstream / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Upstream identity mismatch: {relative}")
    if output.exists():
        raise ValueError("Preparation output must be fresh")
    output.mkdir(parents=True)
    for relative in manifest["files"]:
        if relative.startswith("recomp/"):
            target = output / relative.removeprefix("recomp/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(upstream / relative, target)
    shutil.copytree(ROOT / "games/mk8d", output / "games/mk8d")
    adapt_modules(output, profile_path)
    replace(output, "runtime/net/vlan.c", "s > 3", "s > 11")
    # Menu options that belong to other games (nothing in MK8 reads them); Hero Mode and blur are already behind GAME_LA_SETTINGS.
    menu = "runtime/ui/menu.mm"
    replace(output, menu, '    opt_choice("Aspect ratio"', '    if (0) opt_choice("Aspect ratio"')
    replace(output, menu, '    if (opt_toggle("Skip intro"', '    if (0 && opt_toggle("Skip intro"')
    replace(output, menu, '    header("Button prompts");\n    {', '    if (0) {')
    replace(output, menu, '    ImGui::Spacing();\n    header("System language");', '    header("System language");')
    result = {"schema": 2, "upstream_commit": manifest["commit"], "game": "mk8d", "abi": 3, "gameplay_verified": False,
              "adaptations": ["profile-driven module ABI and tables", "loader and constructor candidate", "stop on unimplemented HLE", "twelve VLAN slots", "game-specific modules only", "menu without other games' options"]}
    (output / "MK8-ADOPTION.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def adapt_modules(output, profile_path=None):
    profile = json.loads((profile_path or ROOT / "games/mk8d/profiles/development.json").read_text(encoding="utf-8"))
    modules = profile["modules"]
    names = list(modules)
    if names[:2] != ["main", "sdk"] or profile["init_order"][0] != "sdk" or set(profile["init_order"]) != set(names) or len(profile["init_order"]) != len(names):
        raise ValueError("Invalid module order")
    window = profile["module_window_bytes"]
    ranges = []
    for name, base in modules.items():
        address = int(base, 16)
        if not re.fullmatch(r"main|sdk|subsdk[0-9]+", name) or address < 0x7000000000 or address + window > 0x7080000000:
            raise ValueError("Invalid module address/name")
        if any(address < end and start < address + window for start, end in ranges):
            raise ValueError("Overlapping module windows")
        ranges.append((address, address + window))
    # One profile generates the ABI names, addresses, loader and initialization order.
    header = '#pragma once\n#define RECOMP_MODULE_COUNT %d\n' % len(names)
    header += f'#define RECOMP_MODULE_WINDOW {window}ull\n'
    header += '#define RECOMP_MODULES(X) ' + ' '.join(f'X({name}, {base})' for name, base in modules.items()) + '\n'
    header += '#define RECOMP_INIT_ORDER {' + ', '.join(str(names.index(n)) for n in profile['init_order'][1:]) + '}\n'
    (output / 'runtime/module_config.h').write_text(header, encoding='utf-8')
    if profile.get('display_version'):
        replace(output, 'games/mk8d/game.mk', 'GAME_DISPLAY_VERSION := development', 'GAME_DISPLAY_VERSION := '+profile['display_version'])
    replace(output, 'runtime/gamelib.h', '#define GAMELIB_ABI 1', '#define GAMELIB_ABI 3\n#include "module_config.h"')
    replace(output, 'runtime/gamelib.h', 'typedef struct {\n    uint32_t abi;', '''typedef struct {
    const char *name;
    uint64_t base;
    const uint64_t *addrs;
    const GuestFn *fns;
    const uint64_t *count;
} GameLibModule;

typedef struct {
    uint32_t abi;''')
    replace(output, 'runtime/gamelib.h', '''    const uint64_t *main_addrs, *sdk_addrs;  // sorted guest addresses
    const GuestFn *main_fns, *sdk_fns;       // the functions at them
    const uint64_t *main_count, *sdk_count;''', '    uint32_t module_count;\n    const GameLibModule *modules;')
    (output / 'tools/gamelib_entry.c').write_text('''#include "gamelib.h"
#define DECLARE(name, base) extern const uint64_t name##_fn_addrs[], name##_fn_count; extern const GuestFn name##_fn_ptrs[];
RECOMP_MODULES(DECLARE)
#undef DECLARE
#define TABLE(name, base) {#name, base, name##_fn_addrs, name##_fn_ptrs, &name##_fn_count},
static const GameLibModule modules[] = { RECOMP_MODULES(TABLE) };
GAMELIB_EXPORT const GameLibTables *recomp_game_tables(void) {
    static const GameLibTables t = {GAMELIB_ABI, GAMELIB_ID, RECOMP_MODULE_COUNT, modules};
    return &t;
}
''', encoding='utf-8')
    replace(output, 'runtime/dispatch.c', 'static FnTable tables[2];', 'static FnTable tables[RECOMP_MODULE_COUNT];')
    path = output / 'runtime/dispatch.c'
    text = path.read_text()
    start = text.index('    uint64_t nm = *g->main_count')
    end = text.index('\n}', start)
    text = text[:start] + '''    if (!g || g->abi != GAMELIB_ABI || g->module_count != RECOMP_MODULE_COUNT || !g->modules)
        FATAL("module ABI mismatch\\n");
#define EXPECT(name, base) {#name, base},
    static const struct { const char *name; uint64_t base; } expected[] = { RECOMP_MODULES(EXPECT) };
#undef EXPECT
    for (unsigned i = 0; i < RECOMP_MODULE_COUNT; i++) {
        const GameLibModule *m = &g->modules[i];
        if (!m->name || strcmp(m->name, expected[i].name) || m->base != expected[i].base ||
            !m->count || !*m->count || !m->addrs || !m->fns) FATAL("invalid module table %u\\n", i);
        uint64_t n = *m->count;
        for (uint64_t j = 0; j < n; j++)
            if (!m->fns[j] || m->addrs[j] < m->base || m->addrs[j] - m->base >= RECOMP_MODULE_WINDOW ||
                (j && m->addrs[j] <= m->addrs[j-1])) FATAL("invalid function table %s\\n", m->name);
        tables[i] = (FnTable){m->addrs, m->fns, n, m->addrs[0], m->addrs[n-1]};
    }''' + text[end:]
    text = text.replace('t < 2;', 't < RECOMP_MODULE_COUNT;')
    text = text.replace('uint64_t total = tables[0].count + tables[1].count, k = 0;', 'uint64_t total = 0, k = 0;\n        for (unsigned i = 0; i < RECOMP_MODULE_COUNT; i++) total += tables[i].count;')
    path.write_text(text, encoding='utf-8')
    replace(output, 'runtime/dispatch.c', '    c->x[0] = 0;\n}', '    FATAL("required host service is not implemented: %s\\n", hle_slots[i].name);\n}')
    replace(output, 'runtime/dispatch.c', '-> returning 0', '-> stopping')
    replace(output, 'runtime/rt.h', '#include "cpu.h"', '#include "cpu.h"\n#include "module_config.h"')
    replace(output, 'runtime/rt.h', 'g_modules[4]', 'g_modules[RECOMP_MODULE_COUNT]')
    replace(output, 'runtime/loader.c', 'g_modules[4]', 'g_modules[RECOMP_MODULE_COUNT]')
    replace(output, 'runtime/loader.c', '{{"main", MAIN_BASE}, {"sdk", SDK_BASE}}', '{ RECOMP_MODULES(LOAD_MODULE) }')
    replace(output, 'runtime/loader.c', '    static const struct { const char *name; uint64_t base; } mods[]', '#define LOAD_MODULE(name, base) {#name, base},\n    static const struct { const char *name; uint64_t base; } mods[]')
    replace(output, 'runtime/loader.c', 'for (unsigned i = 0; i < 2; i++)', 'for (unsigned i = 0; i < RECOMP_MODULE_COUNT; i++)')
    replace(output, 'runtime/main.c', 'static void rtld_init_main(Cpu *c) { module_init(c, &g_modules[0]); }', '''// Candidate rtld order, pending boot qualification with the imported build IDs.
static void rtld_init_main(Cpu *c) {
    static const unsigned order[] = RECOMP_INIT_ORDER;
    for (unsigned i = 0; i < sizeof order / sizeof order[0]; i++) module_init(c, &g_modules[order[i]]);
}''')
    for relative in ['runtime/gamelib.c']:
        p = output / relative; text = p.read_text()
        text = re.sub(r'LOG\("\[gamelib\] linked in .*?g_tables->sdk_count\);', 'LOG("[gamelib] linked in (%u modules)\\\\n", g_tables->module_count);', text, flags=re.S)
        text = re.sub(r'LOG\("\[gamelib\] %s \(%llu .*?g_tables->sdk_count\);', 'LOG("[gamelib] %s (%u modules)\\\\n", c[i], g_tables->module_count);', text, flags=re.S)
        p.write_text(text, encoding='utf-8')
    p = output / 'runtime/gamebuild.c'; text = p.read_text()
    text = text.replace('Gen gens[2] = {{.name = "main"}, {.name = "sdk"}};', '#define GEN_MODULE(n, b) {.name = #n},\n    Gen gens[RECOMP_MODULE_COUNT] = { RECOMP_MODULES(GEN_MODULE) };')
    text = text.replace('static const char *const base[2] = {"0x7000000000", "0x7004000000"};', '#define GEN_BASE(n, b) #b,\n    static const char *const base[] = { RECOMP_MODULES(GEN_BASE) };')
    text = text.replace('i < 2', 'i < RECOMP_MODULE_COUNT').replace('queued[2] = {0, 0}', 'queued[RECOMP_MODULE_COUNT] = {0}').replace('tables[2] = {false, false}', 'tables[RECOMP_MODULE_COUNT] = {false}')
    text = text.replace('g->exit == 0 ? 0.5 : g->n ? 0.5 *', 'g->exit == 0 ? 1.0 / RECOMP_MODULE_COUNT : g->n ? (1.0 / RECOMP_MODULE_COUNT) *')
    p.write_text(text, encoding='utf-8')
    replace(output, 'Makefile', 'GEN_SRC  := $(wildcard $(GEN)/main/*.c) $(wildcard $(GEN)/sdk/*.c)', 'GEN_SRC  := ' + ' '.join(f'$(wildcard $(GEN)/{n}/*.c)' for n in names))
    old = '\trm -f $(GEN)/.gen-* && touch $(GEN)/.gen-$(GAMELIB_ID)'
    commands = ''.join(f'\tcd tools && ../$(PY) gen.py {n} ../$(GAMEFILES)/exefs/{n} {modules[n]} ../$(GEN)/{n} $(GENSIMD)\n' for n in names[2:])
    replace(output, 'Makefile', old, commands + old)



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, default=ROOT / "upstream/smm2recomp")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.upstream.resolve(), args.output.resolve(), args.profile), indent=2))
