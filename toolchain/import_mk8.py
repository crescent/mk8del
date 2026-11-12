"""Import the pinned MK8 base and 4.0.1 update from PFS0 (NSZ or NSP)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from repair import cleanup_stale, remove_owned
from storage import GIB, io_workers, require_space
from native import compiler_path

ROOT = Path(__file__).resolve().parents[1]
BASE = "e1ace5248218fa6393ec1637ce8c2c7943fb1d126c2dea93135d641cfd692183"
PATCH = "9b5ca1863da58ddfa3012591878ef6704cc1f39525d50b6c4252c550233a83b5"
CONTROL = "796606c9e2b371b4a3692fb7125dc01f143b26de237a4969f73db5dfe60ba1ce"
SIZES = {BASE: 7242874880, PATCH: 4848707072, CONTROL: 1521152}
ROMFS_BUDGET = 12 * GIB
HEADER = 0x4000
CHUNK = 1024 * 1024


def exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Pacote truncado.")
    return data


class Entry:
    """Bound all reads to one container member, including the Zstandard reader."""
    def __init__(self, stream, offset, size):
        self.stream, self.offset, self.size, self.pos = stream, offset, size, 0

    def read(self, size=-1):
        left = self.size - self.pos
        self.stream.seek(self.offset + self.pos)
        result = self.stream.read(left if size < 0 else min(left, size))
        self.pos += len(result)
        return result


def entries(stream, size):
    magic, count, strings, _ = struct.unpack("<4sIII", exact(stream, 16))
    if magic != b"PFS0" or not 0 < count <= 4096 or not 0 < strings <= 1024**2:
        raise ValueError("Pacote inválido. Selecione um NSZ ou NSP do MK8.")
    table = exact(stream, 24 * count)
    names = exact(stream, strings)
    start = stream.tell()
    result = {}
    for offset, length, name_offset, _ in struct.iter_unpack("<QQII", table):
        name, sep, _ = names[name_offset:].partition(b"\0")
        if not name or not sep or name in result or start + offset + length > size:
            raise ValueError("Tabela de arquivos do pacote inválida.")
        result[name] = (start + offset, length)
    return result


def decoded_blocks(entry, size):
    from zstandard import CONTENTSIZE_UNKNOWN, ZstdDecompressor, frame_content_size

    marker = exact(entry, 8)
    decoder = ZstdDecompressor(max_window_size=128 * 1024 * 1024)
    if marker != b"NCZBLOCK":
        entry.pos -= 8
        with decoder.stream_reader(entry, closefd=False, read_across_frames=True) as stream:
            remaining = size
            while remaining:
                data = exact(stream, min(CHUNK, remaining))
                remaining -= len(data)
                yield data
            if stream.read(1):
                raise ValueError("NCZ maior que o conteúdo esperado.")
        return
    version, kind, _, exponent, count, unpacked = struct.unpack("<BBBBIQ", exact(entry, 16))
    if version != 2 or kind not in (0, 1) or not 14 <= exponent <= 24 or unpacked != size:
        raise ValueError("Cabeçalho de blocos NCZ inválido ou incompatível.")
    block = 1 << exponent
    if count != (size + block - 1) // block:
        raise ValueError("Quantidade de blocos NCZ inválida.")
    lengths = exact(entry, 4 * count)
    for i, (length,) in enumerate(struct.iter_unpack("<I", lengths)):
        expected = min(block, size - i * block)
        if not 0 < length <= expected:
            raise ValueError("Comprimento de bloco NCZ inválido.")
        data = exact(entry, length)
        if length < expected:
            if frame_content_size(data) not in (expected, CONTENTSIZE_UNKNOWN):
                raise ValueError("Tamanho de frame Zstandard inválido.")
            data = decoder.decompress(data, max_output_size=expected, allow_extra_data=False)
        if len(data) != expected:
            raise ValueError("Bloco NCZ truncado.")
        yield data
    if entry.pos != entry.size:
        raise ValueError("Dados extras no NCZ.")


def ncz_chunks(entry, size):
    from Crypto.Cipher import AES

    yield exact(entry, HEADER)
    magic, count = struct.unpack("<8sQ", exact(entry, 16))
    if magic != b"NCZSECTN" or not 0 < count <= 65536:
        raise ValueError("Seções NCZ inválidas.")
    sections = []
    end = HEADER
    for _ in range(count):
        start, length, kind, _, key, counter = struct.unpack("<QQQQ16s16s", exact(entry, 64))
        if start != end or not length or start % 16 or kind not in (1, 3, 4):
            raise ValueError("Layout NCZ não suportado.")
        end += length
        sections.append((end, AES.new(key, AES.MODE_CTR, nonce=counter[:8], initial_value=start // 16)
                         if kind in (3, 4) else None))
    if end != size:
        raise ValueError("Tamanho NCZ não corresponde ao perfil do MK8.")
    i, pos = 0, HEADER
    for block in decoded_blocks(entry, size - HEADER):
        view = memoryview(block)
        while view:
            end, cipher = sections[i]
            length = min(len(view), end - pos)
            data = view[:length]
            yield cipher.encrypt(data) if cipher is not None else data
            view = view[length:]
            pos += length
            if pos == end:
                i += 1


def write_nca(stream, index, digest, target):
    stem = digest[:32].encode()
    member = next((name for name in (stem + b".nca", stem + b".ncz") if name in index), None)
    if member is None:
        raise ValueError("Conteúdo do MK8 base ou da atualização 4.0.1 ausente no pacote.")
    entry = Entry(stream, *index[member])
    size = SIZES[digest]
    if member.endswith(b".nca") and entry.size != size:
        raise ValueError("Tamanho NCA divergente.")
    pieces = ncz_chunks(entry, size) if member.endswith(b".ncz") else iter(lambda: entry.read(CHUNK), b"")
    hashed = hashlib.sha256()
    with target.open("xb") as out:
        for data in pieces:
            out.write(data)
            hashed.update(data)
        if out.tell() != size or hashed.hexdigest() != digest:
            raise ValueError("Hash do conteúdo diverge do perfil 4.0.1.")


def tickets(stream, index):
    for name, (offset, size) in index.items():
        if name.endswith(b".tik") and name.startswith((b"0100152000022000", b"0100152000022800")):
            if not 0x2b0 <= size <= 16384:
                raise ValueError("Ticket do MK8 inválido.")
            stream.seek(offset)
            raw = exact(stream, size)
            if raw[:4] != b"\x04\x00\x01\x00" or name[:-4] != raw[0x2a0:0x2b0].hex().encode():
                raise ValueError("Formato do ticket do MK8 não suportado.")
            yield raw[0x2a0:0x2b0].hex() + " = " + raw[0x180:0x190].hex()


MK8 = b"0100152000022000"
MK8_UPDATE = b"0100152000022800"


def identify(path):
    """What a selected file is, from its headers only: 'base', 'update', 'update-other', 'base-other', 'dlc', 'xci',
    'other-game' or 'not-package', with the PFS0 index when it is one."""
    with Path(path).open("rb") as stream:
        head = stream.read(0x200)
        if Path(path).suffix.lower() in (".xci", ".xcz") or head[0x100:0x104] == b"HEAD":
            return "xci", None
        if head[:4] != b"PFS0":
            return "not-package", None
        stream.seek(0)
        try:
            index = entries(stream, Path(path).stat().st_size)
        except ValueError:
            return "not-package", None
    names = set(index)
    if any(BASE[:32].encode() + ext in names for ext in (b".nca", b".ncz")):
        return "base", index
    if any(PATCH[:32].encode() + ext in names for ext in (b".nca", b".ncz")):
        return "update", index
    tickets = [name for name in names if name.endswith(b".tik")]
    if any(name.startswith(MK8_UPDATE) for name in tickets):
        return "update-other", index
    if any(name.startswith(MK8) for name in tickets):
        return "base-other", index
    if any(name.startswith(MK8[:12]) for name in tickets):  # (MK8 Deluxe's DLC: 01001520000230xx)
        return "dlc", index
    return "other-game", index


def key_names(path):
    """Names in a prod.keys file ("name = value" lines); the values are never read into messages."""
    names = set()
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        name, sep, _ = line.partition("=")
        if sep:
            names.add(name.strip().lower())
    return names


def check_packages(base, patch, keys):
    """Named, specific errors for the usual wrong selections, before the long import. Returns (base, update) in the
    right order: two packages chosen in swapped fields are accepted."""
    kinds = {path: identify(path) for path in (base, patch)}
    if {kinds[base][0], kinds[patch][0]} == {"base", "update"}:
        if kinds[base][0] == "update":
            print("Importação: jogo base e atualização estavam nos campos trocados; usando cada um no seu lugar.", flush=True)
            base, patch = patch, base
    else:
        for path, field in ((base, "base"), (patch, "update")):
            kind = kinds[path][0]
            if kind == field:
                continue
            name = Path(path).name
            if kind == "xci":
                raise ValueError(f"{name} é um XCI/XCZ (cartucho), que não é aceito. Use o NSP/NSZ do jogo base e da "
                                 "atualização 4.0.1, ou selecione uma pasta já extraída.")
            if kind == "not-package":
                raise ValueError(f"{name} não é um pacote NSP/NSZ.")
            if kind == "other-game":
                raise ValueError(f"{name} não é o Mario Kart 8 Deluxe.")
            if kind == "dlc":
                raise ValueError(f"{name} é um DLC do MK8 Deluxe. Selecione o jogo base e a atualização 4.0.1.")
            if kind == "update-other":
                raise ValueError(f"{name} é uma atualização de outra versão do MK8 Deluxe. O MK8-online usa "
                                 "exatamente a atualização 4.0.1.")
            if kind == "base-other":
                raise ValueError(f"{name} é o MK8 Deluxe, mas o conteúdo do jogo base difere do esperado "
                                 "(pacote modificado ou incompleto).")
            if field == "base":  # (an update in both fields)
                raise ValueError("Os dois arquivos são a atualização 4.0.1: falta o jogo base.")
            raise ValueError("Os dois arquivos são o jogo base: falta a atualização 4.0.1.")
    names = key_names(keys)
    if "header_key" not in names:
        raise ValueError("O prod.keys está incompleto (falta header_key). Extraia as chaves de novo do seu console.")
    for path in (base, patch):
        for ticket in kinds[path][1]:
            if ticket.endswith(b".tik") and ticket.startswith((MK8, MK8_UPDATE)):
                revision = max(int(ticket[30:32], 16), 1) - 1  # (the rights ID's last byte: key generation)
                if f"titlekek_{revision:02x}" not in names and f"master_key_{revision:02x}" not in names:
                    raise ValueError(f"O prod.keys não tem as chaves da geração {revision} usada pelo MK8 4.0.1. "
                                     "Extraia as chaves de novo do seu console, com o sistema atualizado.")
    return base, patch


def extract(base, patch, control, keys, title_keys, scratch, output, tool, workers=1):
    data = ("\n".join(title_keys) + "\n").encode("ascii")
    print("Importação: aplicando atualização e extraindo arquivos com verificação de integridade.", flush=True)
    # Tickets travel by pipe. Suppress extractor diagnostics that may contain private material.
    result = subprocess.run([tool / "dotnet/dotnet.exe", tool / "mk8-import.dll", base, patch, control, keys, output,
                             str(workers)], cwd=scratch, input=data,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        if result.returncode == 8:
            raise ValueError("Espaço insuficiente para extrair o RomFS e preservar a reserva de 4 GiB.")
        if result.returncode in (3, 4, 5):  # (the NCAs already matched the 4.0.1 hashes: opening them failed on the keys)
            raise ValueError("O prod.keys não abriu os pacotes: chaves incompletas ou de um sistema anterior à "
                             "atualização 4.0.1. Extraia as chaves de novo do seu console.")
        stage = {6: "metadados", 7: "RomFS", 9: "índice do RomFS", 10: "extração do RomFS"}.get(
            result.returncode, "extrator")
        raise ValueError(f"Falha na importação ({stage}). Confira o prod.keys e os dois pacotes.")


def validate(output, validate_game_identity):
    profile = json.loads((ROOT / "games/mk8d/profiles/4.0.1.json").read_text(encoding="utf-8"))
    exefs = output / "exefs"
    validate_game_identity(exefs / "main.npdm")
    if not ((exefs / "main.npdm").read_bytes()[0xc] & 1):
        raise ValueError("O pacote extraído não é AArch64.")
    for name, expected in profile["input_sha256"].items():
        with (exefs / name).open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise ValueError("ExeFS extraído não corresponde ao MK8 4.0.1.")
    nacp = output / "control/control.nacp"
    romfs = output / "romfs"
    if nacp.read_bytes()[0x3060:0x3070].split(b"\0")[0] != b"4.0.1" or not (romfs / "Course").is_dir():
        raise ValueError("Versão ou arquivos do RomFS inválidos.")
    expected_count, expected_bytes = map(int, (output / "romfs-size.txt").read_text(encoding="ascii").split())
    count = size = 0
    for path in romfs.rglob("*"):
        if path.is_file():
            count += 1
            size += path.stat().st_size
    if expected_count <= 0 or (count, size) != (expected_count, expected_bytes):
        raise ValueError("RomFS incompleto para o perfil 4.0.1.")
    return dict(exefs=str(exefs), romfs=str(romfs), nacp=str(nacp))


def prepare(config, local, validate_game_identity):
    local = compiler_path(local)
    target = local / "imports/mk8-4.0.1"
    cleanup_stale(local / "imports", "mk8-stage-*")  # (an import cut off before it finished)
    base, patch, keys = (Path(config.get(name) or ".").resolve()
                         for name in ("base_package", "update_package", "prod_keys"))
    packages = all(p.is_file() for p in (base, patch, keys)) and base != patch
    if target.is_dir():
        try:
            paths = validate(target, validate_game_identity)
            print("Importação: reaproveitando arquivos extraídos.", flush=True)
            return {**config, **paths}
        except (ValueError, FileNotFoundError) as error:
            # Our own extraction, incomplete or changed: extracted again from the packages instead of stopping.
            if not packages:
                raise ValueError(f"A importação anterior está incompleta ({error}). Selecione o jogo base, "
                                 "a atualização e o prod.keys para extrair de novo.") from error
            print(f"Importação anterior incompleta ({error}); extraindo de novo.", flush=True)
            remove_owned(target, local / "imports")
    if not packages:
        if not base.is_file():
            raise ValueError("Jogo base não selecionado (ou o arquivo não existe mais).")
        if not patch.is_file():
            raise ValueError("Atualização 4.0.1 não selecionada (ou o arquivo não existe mais).")
        if base == patch:
            raise ValueError("O mesmo arquivo foi escolhido para o jogo base e para a atualização.")
        raise ValueError("prod.keys não selecionado (ou o arquivo não existe mais).")
    base, patch = check_packages(base, patch, keys)
    reserve = 4 * GIB + sum(SIZES.values()) + ROMFS_BUDGET + 128 * 1024**2
    try:
        require_space(local, reserve)
    except ValueError as error:
        raise ValueError(f"A primeira importação requer {reserve / GIB:.1f} GiB livres no disco do launcher, "
                         "incluindo 4 GiB de reserva. Também é possível selecionar uma pasta já extraída.") from error
    try:
        crypto_ready = version("pycryptodome") == "3.23.0" and version("zstandard") == "0.25.0"
    except PackageNotFoundError:
        crypto_ready = False
    if not crypto_ready:
        print("Importação: preparando bibliotecas verificadas.", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--require-hashes",
                        "-r", ROOT / "toolchain/requirements-host.txt"], check=True)
    tool = ROOT / "tools/mk8-import"
    if not (tool / "mk8-import.dll").is_file():
        subprocess.run([sys.executable, ROOT / "scripts/setup-import-tool.py"], check=True)
    local.joinpath("imports").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mk8-stage-", dir=local / "imports") as temp, ExitStack() as stack:
        scratch = Path(temp)
        output = scratch / "output"
        output.mkdir()
        a, b = (stack.enter_context(path.open("rb")) for path in (base, patch))
        base_index, patch_index = entries(a, base.stat().st_size), entries(b, patch.stat().st_size)
        title_keys = [*tickets(a, base_index), *tickets(b, patch_index)]
        print("Importação: reconstruindo os arquivos NCA dos pacotes.", flush=True)
        files = [scratch / name for name in ("base.nca", "patch.nca", "control.nca")]
        # The three NCAs at once, each through its own file handle (Zstandard, AES and SHA-256 release the GIL):
        # the base no longer waits for the update.
        jobs = list(zip((base, patch, patch), (base_index, patch_index, patch_index), (BASE, PATCH, CONTROL), files))

        def rebuild(job):
            package, index, digest, file = job
            with package.open("rb") as stream:
                write_nca(stream, index, digest, file)

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            list(pool.map(rebuild, jobs))
        # The RomFS is written by several threads (each with its own reader and the same integrity check).
        extract(*files, keys, title_keys, scratch, output, tool, io_workers(local))
        validate(output, validate_game_identity)
        output.rename(target)
    print("Importação 4.0.1 verificada.", flush=True)
    return {**config, **validate(target, validate_game_identity)}
