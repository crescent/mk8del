"""Compile generated AArch64 C to a resumable Windows object archive (not a playable app)."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
# The launcher shares one tool folder between its unpacked versions (no junction: WinError 448).
TOOLS = Path(os.environ.get('MK8_NATIVE_TOOLS') or ROOT / 'local/native-tools')
DEFAULT_COMPILER = TOOLS / 'zig-x86_64-windows-0.17.0/zig.exe'


def compiler_path(path):
    # Rust canonicalize supplies extended Windows paths; clang's -I does not accept that prefix.
    value = str(path)
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return Path(value)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


DEFAULT_OBJECT_CACHE = TOOLS / 'objects'


INCLUDE = re.compile(rb'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"]+)[>"]', re.M)


def included_headers(sources, include_dir, root):
    """Name and digest of every header the sources reach by #include, resolved like the compiler (the including
    file's folder, then -I include_dir); system headers are left to the compiler's digest. A header nothing compiled
    here includes (UI, services) no longer invalidates the objects of the game code."""
    found, pending = {}, list(sources)
    while pending:
        path = pending.pop()
        for name in set(INCLUDE.findall(Path(path).read_bytes())):
            name = name.decode()
            for folder in (Path(path).parent, include_dir):
                header = (folder/name).resolve()
                if header.is_file():
                    if header not in found:
                        found[header] = digest(header)
                        pending.append(header)
                    break
    return {(str(p.relative_to(root)) if p.is_relative_to(root) else str(p)): d for p, d in sorted(found.items())}


def cache_key(key):
    """Content address of an object: unit source, runtime headers, compiler and flags, without the job path.

    The job-specific runtime include path (local/builds/<identity>/runtime/runtime) is the only absolute path in the
    flags; the headers behind it are already hashed by name and content in the environment identity.
    """
    flags = ['-I<runtime>' if flag.startswith('-I') else flag for flag in key['flags']]
    return hashlib.sha256(json.dumps({'source': key['source'], 'environment': key['environment'], 'flags': flags},
                                     sort_keys=True).encode()).hexdigest()


def _place(src, dst):
    """Atomically make dst a hard link to src (a copy where links are unavailable)."""
    pending = dst.with_name(dst.name + f'.{os.getpid()}.{threading.get_ident()}.link')
    pending.unlink(missing_ok=True)
    try:
        os.link(src, pending)
    except OSError:
        shutil.copyfile(src, pending)
    pending.replace(dst)


def cache_lookup(cache, address):
    """Path of a verified cached object, or None. A mismatching entry is ignored, never trusted."""
    obj, receipt = cache/address[:2]/(address + '.o'), cache/address[:2]/(address + '.json')
    if not (obj.is_file() and receipt.is_file()):
        return None
    try:
        record = json.loads(receipt.read_text(encoding='utf-8'))
        return obj if isinstance(record, dict) and record.get('sha256') == digest(obj) else None
    except (FileNotFoundError, ValueError):
        return None


def cache_store(cache, address, obj, sha256):
    folder = cache/address[:2]
    folder.mkdir(parents=True, exist_ok=True)
    _place(obj, folder/(address + '.o'))
    receipt = folder/(address + f'.{os.getpid()}.{threading.get_ident()}.pending')
    receipt.write_text(json.dumps({'sha256': sha256}), encoding='utf-8')
    receipt.replace(folder/(address + '.json'))


def seed_cache(builds, cache):
    """Register objects of earlier builds (receipt and hash verified) so a new build identity reuses them.

    Only the native object folders are read: <build>/native*/<module>/*.json and <build>/native*/*.json; junctions
    elsewhere in a build folder are never followed. Returns the number of objects added.
    """
    added = 0
    for folder in sorted(Path(builds).glob('*/native*')):
        if not folder.is_dir() or folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
            continue
        for stamp in [*folder.glob('*.json'), *folder.glob('*/*.json')]:
            try:
                receipt = json.loads(stamp.read_text(encoding='utf-8'))
                key = receipt['input']
                address = cache_key(key)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            obj = stamp.with_suffix('.o')
            # Existence is enough here (cheap on later runs); every use re-verifies the hash in cache_lookup.
            if (cache/address[:2]/(address + '.json')).is_file() or not obj.is_file() \
                    or digest(obj) != receipt.get('sha256'):
                continue
            cache_store(cache, address, obj, receipt['sha256'])
            added += 1
    return added


def default_workers():
    env = os.environ.get('MK8_NATIVE_JOBS')
    if env:
        jobs = int(env)
        if not 1 <= jobs <= 16:
            raise ValueError('MK8_NATIVE_JOBS deve estar entre 1 e 16')
        return jobs
    logical = os.cpu_count() or 2
    if os.name != 'nt':
        return max(1, min(8, logical // 2))
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32),
                    ('total', ctypes.c_uint64), ('available', ctypes.c_uint64),
                    ('other', ctypes.c_uint64 * 5)]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return max(1, min(8, logical // 2))
    # Reserve 1 GiB for Windows and budget 320 MiB per compiler.
    budget = (status.available - 1024**3) // (320 * 1024**2)
    return max(1, min(16, logical - 2, budget))


def compiler_env():
    return {'ZIG_GLOBAL_CACHE_DIR': str(TOOLS/'cache'), **os.environ}


def compile_priority(source):
    size = source.stat().st_size
    if size < 128 * 1024 or source.stem.endswith('_table'):
        return 0, size
    # One large guest function can take much longer than a file of small functions.
    code = source.read_bytes()
    starts = [match.start() for match in re.finditer(rb'^void f_[^\n]*\{', code, re.M)] + [size]
    longest = max((end - start for start, end in zip(starts, starts[1:])), default=0)
    return (longest if longest >= 128 * 1024 else 0), size


# A generated function definition (gen.py writes one per line, CRLF on Windows; already-marked ones carry a prefix).
DEFINITION = re.compile(rb'^void f_([0-9a-f]+)\(Cpu \*c\) \{\r?$', re.M)
COLD = b'__attribute__((optnone, noinline)) '


def load_hot_functions(path, generated, modules):
    """Profiled guest functions to optimise, or None (optimise all) unless recorded from these exact module inputs."""
    recorded, hot = {}, set()
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.startswith('# input '):
            _, _, name, value = line.split()
            recorded[name] = value
        elif line and not line.startswith('#'):
            hot.add(int(line, 16))
    built = json.loads((Path(generated)/'MODULES.json').read_text(encoding='utf-8'))
    if not hot or set(recorded) != set(modules) or any(built[name]['sha256'] != recorded[name] for name in modules):
        print('Lista de funções quentes de outros módulos: compilando todas as funções otimizadas.', flush=True)
        return None
    return frozenset(hot)


def mark_cold(src, target, hot):
    """Unit copy where functions outside `hot` get optnone: clang skips optimising them (the giant ones take minutes)."""
    text = src.read_bytes()
    marked = DEFINITION.sub(lambda m: m[0] if int(m[1], 16) in hot else COLD + m[0], text)
    if marked == text:
        return src
    if not target.is_file() or target.read_bytes() != marked:
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix('.pending')
        pending.write_bytes(marked)
        pending.replace(target)
    return target


def compile_modules(source, generated, output, modules, compiler=DEFAULT_COMPILER, optimization='-O0', workers=None,
                    cache=DEFAULT_OBJECT_CACHE, hot_functions=None):
    """hot_functions: optional profile list (load_hot_functions); other functions are compiled without optimisation."""
    source, generated, output = map(compiler_path, (source, generated, output))
    if optimization not in ('-O0', '-O1', '-O2'):
        raise ValueError('Invalid native optimization level')
    if workers is None:
        workers = default_workers()
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError('Native compiler workers must be between 1 and 16')
    compiler = Path(compiler).resolve()
    from storage import require_space
    require_space(output)
    if not compiler.is_file():
        raise ValueError('Compilador nativo ausente: execute python scripts/setup-native.py')
    output.mkdir(parents=True, exist_ok=True)
    flags = ['-target', 'x86_64-windows-gnu', '-mcpu=x86_64_v2', optimization, '-fno-strict-aliasing',
             '-Wno-return-type', '-Wno-unused-label', '-D_FILE_OFFSET_BITS=64', '-I' + str(source/'runtime')]
    if optimization != '-O0':
        flags.append('-g0')
    entries = []
    for name in modules:
        files = sorted((generated/name).glob('*.c'))
        if not files or not (generated/name/(name+'_table.c')).is_file():
            raise ValueError(f'Módulo gerado incompleto: {name}')
        entries.extend((p, output/name/(p.stem+'.o')) for p in files)
    entries.append((source/'tools/gamelib_entry.c', output/'gamelib_entry.o'))
    if 'sdk' in modules:
        from sdk_exports import supplement
        extra = supplement(source, generated, output)
        if extra:
            code, table = extra
            entries = [(table if src == generated/'sdk/sdk_table.c' else src, obj) for src, obj in entries]
            entries.append((code, output/'sdk/sdk_assembly.o'))
    headers = included_headers([src for src, _ in entries], source/'runtime', source)
    identity = hashlib.sha256(json.dumps({'headers': headers, 'compiler': digest(compiler)}, sort_keys=True).encode()).hexdigest()
    hot = load_hot_functions(hot_functions, generated, modules) if hot_functions and optimization != '-O0' else None
    env = compiler_env()
    cache = Path(cache)
    # Builds live in <local>/builds/<identity>/<native folder>; earlier ones seed the shared cache.
    seeded = seed_cache(output.parent.parent, cache) if output.parent.parent.name == 'builds' else 0
    if seeded:
        print(f'Cache de objetos: {seeded} objetos de builds anteriores registrados.', flush=True)
    reused = []

    def compile_one(pair):
        src, obj = pair
        module = src.parent.name
        from experimental import patch_no_mii_unit
        src = patch_no_mii_unit(src, output)
        if hot is not None:
            src = mark_cold(src, output/'tiered'/module/src.name, hot)
        obj.parent.mkdir(parents=True, exist_ok=True)
        stamp = obj.with_suffix('.json')
        key = {'source': digest(src), 'environment': identity, 'flags': flags}
        if obj.exists() and stamp.exists():
            try:
                receipt = json.loads(stamp.read_text(encoding='utf-8'))
                if isinstance(receipt, dict) and receipt.get('input') == key and receipt.get('sha256') == digest(obj):
                    return obj
            except (FileNotFoundError, ValueError):
                pass  # Interrupted metadata is not proof that an object can be reused.
        address = cache_key(key)
        cached = cache_lookup(cache, address)
        if cached:
            _place(cached, obj)
            record = {'input': key, 'sha256': digest(obj), 'cache': address}
            temp = stamp.with_suffix('.pending'); temp.write_text(json.dumps(record), encoding='utf-8'); temp.replace(stamp)
            reused.append(obj)
            return obj
        pending = obj.with_suffix('.pending.o')
        require_space(output)
        args = [str(compiler), 'cc', *flags, '-c', str(src), '-o', str(pending)]
        started = time.perf_counter()
        result = subprocess.run(args, capture_output=True, env=env)
        seconds = round(time.perf_counter() - started, 2)
        obj.with_suffix('.log').write_bytes(result.stdout+result.stderr)
        if result.returncode:
            raise ValueError(f'Compilação falhou: {src.name}\n' + result.stderr.decode('utf-8', 'replace')[-4000:])
        pending.replace(obj)
        record = {'input': key, 'sha256': digest(obj), 'seconds': seconds, 'cache': address}
        cache_store(cache, address, obj, record['sha256'])
        temp = stamp.with_suffix('.pending'); temp.write_text(json.dumps(record), encoding='utf-8'); temp.replace(stamp)
        return obj

    jobs = min(workers, os.cpu_count() or 1)
    # Keep the archive order stable while starting the larger units first.
    ordered = sorted(entries, key=lambda entry: compile_priority(entry[0]), reverse=True)
    print(f'Compilação nativa: {jobs} compiladores, {len(entries)} unidades.', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for i, future in enumerate(concurrent.futures.as_completed(pool.submit(compile_one, entry) for entry in ordered), 1):
            future.result()
            print(f'Compilação nativa: {i}/{len(entries)} unidades', flush=True)
    objects = [obj for _, obj in entries]
    if reused:
        print(f'Cache de objetos: {len(reused)} de {len(entries)} unidades reaproveitadas sem recompilar.', flush=True)
    response = output/'archive.rsp'
    response.write_text('\n'.join('"'+str(p).replace('\\','/')+'"' for p in objects), encoding='utf-8')
    archive = output/'mk8d-game-code.a'
    pending = output/'mk8d-game-code.pending.a'
    # Thin archives reference cached objects instead of duplicating several GB of debug code.
    subprocess.run([str(compiler), 'ar', 'rcsT', str(pending), '@'+str(response)], check=True, env=env)
    pending.replace(archive)
    receipt = {'state': 'native_objects', 'archive': str(archive), 'sha256': digest(archive),
               'object_files': [{'path': str(p), 'sha256': digest(p)} for p in objects],
               'modules': list(modules), 'objects': len(objects), 'optimization': optimization,
               'hot_functions': len(hot) if hot is not None else None, 'gameplay_verified': False,
               'remaining': ['link with native graphics and platform runtime', 'boot and race qualification']}
    (output/'native-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt
