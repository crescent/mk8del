"""AArch64 AOT pipeline: validate, generate, compile and publish an experimental candidate."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import struct
from pathlib import Path
import subprocess
import sys
import time

from repair import cleanup_stale, remove_owned, unlink_links_on_the_way, is_link
from build_lock import exclusive_build
from storage import require_space, describe_io_error

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def file_record(path):
    return {'path': str(path.resolve()), 'sha256': sha(path), 'bytes': path.stat().st_size}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".json.pending")
    pending.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(pending, path)


def python_tool():
    path = ROOT / "local/python/Scripts/python.exe"
    return str(path) if path.is_file() else sys.executable


def select_profile(config):
    main = Path(config.get("exefs") or ".") / "main"
    if config.get("exefs") and main.is_file():
        identity = sha(main)
        for path in sorted((ROOT / "games/mk8d/profiles").glob("*.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(profile, dict) and profile.get("input_sha256", {}).get("main") == identity:  # (not a patch list)
                return path
    return ROOT / "games/mk8d/profiles/development.json"


def preflight(config):
    errors = []
    upstream = Path(config.get("upstream") or ROOT / "upstream/smm2recomp")
    if not (upstream / "recomp/tools/gen.py").is_file():
        errors.append("Fontes do runtime ausentes; selecione a raiz que contém recomp/tools/gen.py")
    profile = json.loads(select_profile(config).read_text(encoding="utf-8"))
    for name in (*profile["modules"], "main.npdm"):
        if not config.get("exefs") or not (Path(config["exefs"]) / name).is_file():
            errors.append(f"ExeFS do MK8 ausente: {name}")
    npdm = Path(config.get("exefs") or ".") / "main.npdm"
    if config.get("exefs") and npdm.is_file():
        try:
            validate_game_identity(npdm)
        except ValueError as error:
            errors.append(str(error))
        unexpected = sorted(p.name for p in npdm.parent.glob("subsdk*")
                            if p.is_file() and p.name not in profile["modules"])
        if unexpected:
            errors.append("Runtime ainda não contempla estes módulos do jogo: " + ", ".join(unexpected))
        for name, expected in profile.get("input_sha256", {}).items():
            path = npdm.parent / name
            if path.is_file() and sha(path) != expected:
                errors.append(f"Módulo {name} não corresponde ao perfil selecionado; não misture versões.")
    result = subprocess.run([python_tool(), "-c", "import capstone, lz4.block, unicorn"], capture_output=True)
    if result.returncode:
        errors.append("Dependências de geração ausentes. Execute scripts/setup-tools.ps1")
    return errors


def validate_game_identity(path):
    raw = Path(path).read_bytes()
    if len(raw) < 0x80 or raw[:4] != b"META":
        raise ValueError("main.npdm inválido: cabeçalho META ausente ou truncado")
    offset, size = struct.unpack_from("<II", raw, 0x70)
    if offset < 0x80 or size < 0x38 or offset + size > len(raw) or raw[offset:offset+4] != b"ACI0":
        raise ValueError("main.npdm inválido: seção ACI0 fora dos limites ou ausente")
    title_id = struct.unpack_from("<Q", raw, offset + 0x10)[0]
    if title_id != 0x0100152000022000:
        raise ValueError(f"Jogo incorreto: Title ID {title_id:016X}; esperado MK8 Deluxe 0100152000022000")
    if not raw[0x0c] & 1:
        raise ValueError("ExeFS AArch32 (32 bits): este tradutor requer AArch64 (64 bits). Importe uma atualização AArch64 do MK8; renomear módulos não converte a arquitetura.")
    return f"{title_id:016X}"


def walk_error(error):
    raise error


def runtime_matches(prepared, receipt):
    try:
        expected = json.loads(receipt.read_text(encoding='utf-8'))
        if is_link(prepared) or not isinstance(expected, dict) or not expected or not (prepared/'MK8-ADOPTION.json').is_file():
            return False
        actual = {}
        for folder, dirs, files in os.walk(prepared, onerror=walk_error, followlinks=False):
            for name in [*dirs, *files]:
                if is_link(Path(folder)/name):
                    return False
            for name in files:
                path = Path(folder)/name
                if '__pycache__' not in path.parts:
                    actual[str(path.relative_to(prepared))] = sha(path)
        return actual == expected
    except (FileNotFoundError, ValueError):
        return False


def prepare_runtime(job, upstream, profile_path):
    require_space(job)
    prepared = job / "runtime"
    print("Etapa: validar e adaptar runtime", flush=True)
    cleanup_stale(job, "runtime-pending-*")  # (a preparation cut off before it finished)
    if prepared.exists() and not runtime_matches(prepared, job / "runtime-hashes.json"):
        print("Runtime sem recibo válido ou alterado; preparando novamente a partir das fontes verificadas.", flush=True)
        remove_owned(prepared, job)
    if not (prepared / "MK8-ADOPTION.json").is_file():
        stage = job / f"runtime-pending-{time.time_ns()}"
        subprocess.run([sys.executable, ROOT / "runtime/prepare.py", "--upstream", upstream, "--output", stage, "--profile", profile_path], check=True)
        stage.rename(prepared)
    else:
        print("Runtime preparado anteriormente; verificado.", flush=True)
    runtime_hashes = {str(p.relative_to(prepared)): sha(p) for p in prepared.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    atomic_json(job / "runtime-hashes.json", runtime_hashes)
    return prepared


def build(config, local):
    require_space(local)
    started = time.perf_counter()
    if config.get("base_package") or config.get("update_package"):
        from import_mk8 import prepare
        config = prepare(config, local, validate_game_identity)
    print("MK8: verificando entradas e runtime.", flush=True)
    errors = preflight(config)
    if errors:
        raise ValueError("Preparação bloqueada:\n- " + "\n- ".join(errors))
    profile_path = select_profile(config)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    # Optional profile of the game's hot functions; the rest compile without optimisation (toolchain/native.py).
    hot_list = profile_path.with_name(profile_path.stem + "-hot-functions.txt")
    # Optional instruction patches, each checked against the module's original word (toolchain/recompiler/generate.py).
    patches = profile_path.with_name(profile_path.stem + "-patches.json")
    upstream = Path(config.get("upstream") or ROOT / "upstream/smm2recomp").resolve()
    inputs = {name: sha(Path(config["exefs"]) / name) for name in (*profile["modules"], "main.npdm")}
    inputs["profile"] = sha(profile_path)
    inputs["adapter"] = sha(ROOT / "runtime/prepare.py")
    inputs["generator"] = sha(ROOT / "toolchain/recompiler/generate.py")
    inputs["upstream"] = sha(ROOT / "upstream/manifests/smm2recomp.json")
    for p in sorted((ROOT / "games/mk8d").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            inputs[str(p.relative_to(ROOT))] = sha(p)
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    job = local / "builds" / identity
    job.mkdir(parents=True, exist_ok=True)
    # Keep imported code inputs separate from the user's original files.
    imported = job / "inputs/exefs"
    imported.mkdir(parents=True, exist_ok=True)
    for name in (*profile["modules"], "main.npdm"):
        dest = imported / name
        if not dest.is_file() or sha(dest) != inputs[name]:
            pending = imported / (name + ".pending")
            shutil.copyfile(Path(config["exefs"]) / name, pending)
            if sha(pending) != inputs[name]:
                raise ValueError(f"Arquivo de origem alterado durante importação: {name}")
            os.replace(pending, dest)
    validate_game_identity(imported / "main.npdm")
    prepared = prepare_runtime(job, upstream, profile_path)
    from host import build_host, prepare_dll_link
    from native import compile_modules, default_workers

    # The host only needs the prepared runtime. Start its cold C++/CRT link while guest code is compiling.
    with ThreadPoolExecutor(max_workers=2) as pool:
        host_workers = min(4, max(1, default_workers() // 3))
        host = pool.submit(build_host, prepared, job / "host-optimized", workers=host_workers)
        dll_link = pool.submit(prepare_dll_link, job / "linker-check")
        print("Etapa: traduzir AArch64 para C e preparar runtime Windows em paralelo.", flush=True)
        subprocess.run([python_tool(), ROOT / "toolchain/recompiler/generate.py", "--source", prepared, "--exefs", imported, "--output", job / "generated", "--profile", profile_path,
                        *(["--patches", patches] if patches.is_file() else [])], check=True)
        receipt = {"identity": identity, "inputs": inputs, "source": str(prepared), "generated": str(job / "generated"), "state": "generated", "gameplay_verified": False,
                   "remaining": ["link native graphics and game code", "boot and complete a race"]}
        atomic_json(job / "generation-receipt.json", receipt)
        atomic_json(local / "generation.json", receipt)
        generated_at = time.perf_counter()
        print(f"Geração C concluída em {generated_at-started:.1f} s. Compilando código nativo Windows.", flush=True)
        if host.done():
            host.result()
        result = compile_modules(prepared, job / "generated", job / "native-optimized", profile["modules"],
                                 optimization='-O1', cache=local / "native-tools/objects",
                                 hot_functions=hot_list if hot_list.is_file() else None)
        result["identity"] = identity
        atomic_json(local / "native.json", result)
        native_at = time.perf_counter()
        print(f"Compilação nativa concluída em {native_at-generated_at:.1f} s.", flush=True)
        dll_link.result()
        finish(config, local, executable=host.result())
    finished = time.perf_counter()
    print(f"Runtime e ligação: {finished-native_at:.1f} s. Preparação total: {finished-started:.1f} s.", flush=True)


def mirror(path, target, copy_ok=False):
    """Expose `target` at `path` as real folders with hard-linked files, never a junction.

    The launcher runs with Windows' RedirectionTrust mitigation (GetProcessMitigationPolicy reports
    EnforceRedirectionTrust=1): it and the processes it starts refuse junctions created without
    administrator rights (WinError 448). A junction to the RomFS made the launcher report
    "Instalação incompleta: RomFS ausente" right after a successful build. Hard links are not
    reparse points and take no extra space, but need the same volume: a small tree (`copy_ok`) is
    copied instead; a large one fails with the reason rather than copying gigabytes.
    """
    path, target = Path(path), Path(target).resolve(strict=True)
    # Enumerate successfully before replacing or pruning anything. pathlib.rglob can
    # silently skip inaccessible directories, which must not be interpreted as deletion.
    sources = []
    for folder, dirs, files in os.walk(target, onerror=walk_error, followlinks=False):
        for name in dirs:
            if is_link(Path(folder)/name):
                raise ValueError(f'Pasta de origem contém link: {Path(folder)/name}')
        for name in files:
            src = Path(folder)/name
            src.stat()
            sources.append(src)
    if not target.is_dir():
        raise ValueError(f'Pasta de origem indisponível: {target}')
    if path.is_junction() or path.is_symlink():
        os.rmdir(path)  # only the link left by older builds; its target is untouched
    wanted = set()
    for src in sources:
        rel = src.relative_to(target)
        dst = path/rel
        wanted.add(rel)
        if dst.is_file():
            if os.path.samefile(src, dst):
                continue
            a, b = src.stat(), dst.stat()
            if copy_ok and (a.st_size, a.st_mtime_ns) == (b.st_size, b.st_mtime_ns):
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        pending = dst.with_name(dst.name + f'.pending-{os.getpid()}-{time.time_ns()}')
        try:
            os.link(src, pending)
        except OSError as error:
            import errno
            unsupported = error.errno in (errno.EXDEV, errno.ENOTSUP) or getattr(error, 'winerror', None) in (1, 17, 50)
            if not unsupported:
                raise
            if not copy_ok:
                raise ValueError(f'{target} precisa estar no mesmo disco da pasta de dados ({path.anchor}): {error}') from error
            shutil.copy2(src, pending)
        os.replace(pending, dst)
    target.stat()  # A disconnected source is not evidence that old destination files are stale.
    # Files from an older import are links/copies owned by this folder only.
    for dst in path.rglob('*') if path.is_dir() else ():
        if dst.is_file() and dst.relative_to(path) not in wanted:
            dst.unlink()


def prepare_system_data(config, local):
    if config.get('fonts'):
        mirror(local/'user-no-mii/fonts', config['fonts'], copy_ok=True)


def boot_check(command, gamelib, local, timeout=240):
    """Start a new build in a scratch data folder, in a hidden window, until it captures the title screen (frame 360).

    A crash, hang or exit before that keeps candidate.json unchanged and leaves the game's output in
    <local>/boot-check.log. MK8_SKIP_BOOT_CHECK=1 skips it (machines without a usable Vulkan driver).
    """
    if os.environ.get('MK8_SKIP_BOOT_CHECK') == '1':
        print('Teste de inicialização pulado (MK8_SKIP_BOOT_CHECK=1).', flush=True)
        return
    import tempfile
    print('Etapa: abrir o jogo até a tela-título (janela oculta).', flush=True)
    log = local/'boot-check.log'
    with tempfile.TemporaryDirectory(prefix='mk8-boot-') as scratch:
        shot = Path(scratch)/'title.png'
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('MK8_', 'LARECOMP_', 'RECOMP_', 'NX_LAN_'))}
        env.update(MK8_EXPERIMENTAL_NO_MII='1', LARECOMP_DATA_ROOT=scratch, LARECOMP_GAME_LIB=str(gamelib),
                   LARECOMP_HIDDEN='1', LARECOMP_NO_USER_INPUT='1', LARECOMP_WINDOW_SHOT='360:360:1',
                   LARECOMP_SHOT_PATH=str(shot), LARECOMP_EXIT_AFTER_SHOT='1')
        started = time.perf_counter()
        with log.open('wb') as stream:
            child = subprocess.Popen([str(part) for part in command], cwd=scratch, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT)
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # (the runtime may re-execute itself as a child: end the whole tree)
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                child.kill()
                child.wait()
        if child.returncode or not shot.is_file():
            fatal = [line for line in log.read_text(encoding='utf-8', errors='replace').splitlines() if 'FATAL' in line]
            reason = fatal[-1] if fatal else f'saída {child.returncode}' if child.returncode else 'sem tela-título'
            raise ValueError(f'A build nova não chegou à tela-título ({reason}); candidato não publicado. Log: {log}')
    print(f'Tela-título alcançada em {time.perf_counter() - started:.1f} s.', flush=True)


def finish(config, local, executable=None):
    # A candidate enables only the explicit diagnostic launch, never normal/online play.
    from host import build_host, link_game
    from native import compiler_path
    generated = json.loads((local/'generation.json').read_text(encoding='utf-8'))
    native = json.loads((local/'native.json').read_text(encoding='utf-8'))
    if native['identity'] != generated['identity'] or sha(native['archive']) != native['sha256']:
        raise ValueError('Objetos nativos não correspondem à geração atual')
    for obj in native.get('object_files', []):
        if sha(obj['path']) != obj['sha256']:
            raise ValueError('Objeto nativo alterado: '+obj['path'])
    source = compiler_path(generated['source']); job = source.parent
    for name in ('main', 'sdk', 'subsdk0', 'main.npdm'):
        if sha(job/'inputs/exefs'/name) != generated['inputs'][name]:
            raise ValueError('Entrada alterada: '+name)
    romfs = Path(config.get('romfs') or '')
    nacp = Path(config.get('nacp') or '')
    if not config.get('romfs') or not (romfs/'Course').is_dir():
        raise ValueError('RomFS do MK8 ausente ou incompleto')
    if not nacp.is_file() or nacp.read_bytes()[0x3060:0x3070].split(b'\0')[0] != b'4.0.1':
        raise ValueError('Esta build experimental exige NACP do MK8 4.0.1')
    output = job/('host-debug' if native['optimization'] == '-O0' else 'host-optimized')
    exe = executable if executable is not None else build_host(source, output)
    dll = link_game(native['archive'], output, source, native['modules'])
    subprocess.run([python_tool(), ROOT/'scripts/runtime-dlls.py', output], check=True)
    # Deep build hashes made Win32 stat silently omit 192 RomFS files at MAX_PATH.
    data = local/'game-data'/generated['identity'][:16]
    if os.name == 'nt' and any(len(str(data/'extracted/romfs'/p.relative_to(romfs))) >= 260 for p in romfs.rglob('*') if p.is_file()):
        raise ValueError('Caminho do RomFS excede MAX_PATH; use uma pasta local mais curta para os testes')
    print('Etapa: preparar os dados do jogo.', flush=True)
    for removed in [*unlink_links_on_the_way(data/'extracted/exefs', local), *unlink_links_on_the_way(data/'extracted/romfs', local)]:
        print(f'Link antigo removido (só o link): {removed}', flush=True)
    mirror(data/'extracted/exefs', job/'inputs/exefs')
    mirror(data/'extracted/romfs', romfs)
    prepare_system_data(config, local)
    files = {p.name: file_record(p) for p in output.glob('*.dll')}
    files['gamelib'] = files.pop(dll.name)
    files['gamelib-id'] = file_record(output/'mk8dgame.dll.id')
    files['nacp'] = file_record(nacp)
    for name in ('main', 'sdk', 'subsdk0', 'main.npdm'):
        files['npdm' if name == 'main.npdm' else name] = file_record(job/'inputs/exefs'/name)
    boot_check([exe, data/'extracted'], dll, local)
    executable = file_record(exe)
    identity = hashlib.sha256(json.dumps({'generation': generated['identity'], 'files': files, 'exe': executable}, sort_keys=True).encode()).hexdigest()
    candidate = {'schema': 2, 'identity': identity, 'profile': 'mk8d-smm2-experimental', 'executable': executable,
                 'files': files, 'data_root': str(data), 'shader_root': str(local/'user/shaders'), 'gameplay_verified': False}
    atomic_json(local/'candidate.json', candidate)
    print('Build experimental integrada. Use Testar solo experimental; nenhuma corrida está qualificada.', flush=True)


def refresh_runtime(local):
    """Relink only host-side fixes; the generated code and ABI must remain unchanged."""
    from host import build_host
    from native import compiler_path
    generated = json.loads((local/'generation.json').read_text(encoding='utf-8'))
    candidate = json.loads((local/'candidate.json').read_text(encoding='utf-8'))
    source = compiler_path(generated['source'])
    for rel, expected in json.loads((source.parent/'runtime-hashes.json').read_text(encoding='utf-8')).items():
        if sha(source/rel) != expected:
            raise ValueError('Runtime preparado alterado; use uma preparação completa: '+rel)
    output = Path(candidate['executable']['path']).parent
    exe = build_host(source, output)
    for file in candidate['files'].values():
        if sha(file['path']) != file['sha256']:
            raise ValueError('Biblioteca/dado alterado; use --finish: '+file['path'])
    subprocess.run([python_tool(), ROOT/'scripts/runtime-dlls.py', output], check=True)
    candidate['files'] = {name: record for name, record in candidate['files'].items() if not name.lower().endswith('.dll')}
    for dll in output.glob('*.dll'):
        if dll.resolve() != Path(candidate['files']['gamelib']['path']).resolve():
            candidate['files'][dll.name] = file_record(dll)
    boot_check([exe, Path(candidate['data_root'])/'extracted'], candidate['files']['gamelib']['path'], local)
    candidate['executable'] = {'path': str(exe), 'sha256': sha(exe), 'bytes': exe.stat().st_size}
    candidate['identity'] = hashlib.sha256(json.dumps({'generation': generated['identity'], 'files': candidate['files'], 'exe': candidate['executable']}, sort_keys=True).encode()).hexdigest()
    atomic_json(local/'candidate.json', candidate)
    print('Runtime atualizado; biblioteca do jogo preservada. Gameplay não qualificado.', flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--local", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--finish", action="store_true")
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        if args.check:
            errors = preflight(config)
            print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
            sys.exit(bool(errors))
        with exclusive_build(args.local.resolve()):
            if args.runtime_only:
                prepare_system_data(config, args.local.resolve())
                refresh_runtime(args.local.resolve())
            elif args.finish:
                finish(config, args.local.resolve())
            else:
                build(config, args.local.resolve())
    # "Erro: " marks the reason the launcher shows (crates/build-manager: failure_reason).
    except OSError as error:
        print("Erro: " + describe_io_error(error), file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as error:
        print(f"Erro: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
