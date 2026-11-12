"""Small authored containers exercise both NCZ encodings without console data."""
from io import BytesIO
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from Crypto.Cipher import AES
from zstandard import ZstdCompressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolchain"))
from import_mk8 import BASE, PATCH, Entry, HEADER, check_packages, entries, ncz_chunks, prepare


class PackageTests(unittest.TestCase):
    def test_ncz_stream_and_blocks_restore_identical_nca(self):
        block = 1 << 14
        plain = b"a" * block + bytes(range(256))
        key, nonce = b"k" * 16, b"n" * 8
        body = AES.new(key, AES.MODE_CTR, nonce=nonce, initial_value=HEADER // 16).encrypt(plain)
        header = b"h" * HEADER
        sections = b"NCZSECTN" + struct.pack("<Q", 1)
        sections += struct.pack("<QQQQ16s16s", HEADER, len(plain), 3, 0, key, nonce + b"\0" * 8)
        compressor = ZstdCompressor()
        first, last = compressor.compress(plain[:block]), plain[block:]
        blocks = struct.pack("<8sBBBBIQII", b"NCZBLOCK", 2, 1, 0, 14, 2, len(plain), len(first), len(last))
        for encoding in (compressor.compress(plain), blocks + first + last):
            data = header + sections + encoding
            self.assertEqual(b"".join(ncz_chunks(Entry(BytesIO(data), 0, len(data)), HEADER + len(plain))), header + body)
            with self.assertRaises(ValueError):
                list(ncz_chunks(Entry(BytesIO(data), 0, len(data)), HEADER + len(plain) + 1))

    def test_pfs_members_cannot_read_past_container(self):
        name = b"program.ncz\0"
        prefix = struct.pack("<4sIII", b"PFS0", 1, len(name), 0)
        data = prefix + struct.pack("<QQII", 0, 4, 0, 0) + name + b"abcd"
        index = entries(BytesIO(data), len(data))
        self.assertEqual(Entry(BytesIO(data + b"secret"), *index[b"program.ncz"]).read(99), b"abcd")
        with self.assertRaises(ValueError):
            entries(BytesIO(data[:-1]), len(data) - 1)


def pfs(path, names):
    """A PFS0 package holding empty members with these names."""
    table = strings = b""
    for name in names:
        table += struct.pack("<QQII", 0, 0, len(strings), 0)
        strings += name + b"\0"
    path.write_bytes(struct.pack("<4sIII", b"PFS0", len(names), len(strings), 0) + table + strings)
    return path


BASE_NCZ, PATCH_NCZ = BASE[:32].encode() + b".ncz", PATCH[:32].encode() + b".ncz"
BASE_TIK, PATCH_TIK = b"0100152000022000000000000000000a.tik", b"0100152000022800000000000000000a.tik"
KEYS = "header_key = 00\ntitlekek_09 = 00\n"


class SelectionTests(unittest.TestCase):
    """Each wrong selection gets its own message before the long import."""
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.base = pfs(self.root / "base.nsz", [BASE_NCZ, BASE_TIK])
        self.update = pfs(self.root / "update.nsz", [PATCH_NCZ, PATCH_TIK])
        self.keys = self.root / "prod.keys"
        self.keys.write_text(KEYS, encoding="utf-8")

    def tearDown(self):
        self.folder.cleanup()

    def refused(self, message, base=None, update=None):
        with self.assertRaisesRegex(ValueError, message):
            check_packages(base or self.base, update or self.update, self.keys)

    def test_right_and_swapped_selections_are_accepted(self):
        self.assertEqual(check_packages(self.base, self.update, self.keys), (self.base, self.update))
        self.assertEqual(check_packages(self.update, self.base, self.keys), (self.base, self.update))

    def test_cartridge_other_files_and_other_games(self):
        xci = self.root / "game.xci"
        xci.write_bytes(bytes(0x100) + b"HEAD" + bytes(0x100))
        self.refused(r"game\.xci é um XCI/XCZ \(cartucho\)", base=xci)
        renamed = self.root / "game.nsp"
        renamed.write_bytes(xci.read_bytes())
        self.refused(r"game\.nsp é um XCI/XCZ", update=renamed)
        text = self.root / "notes.nsp"
        text.write_bytes(b"not a package")
        self.refused(r"notes\.nsp não é um pacote NSP/NSZ", base=text)
        other = pfs(self.root / "other.nsp", [b"0100000000010000000000000000000a.tik", b"a.nca"])
        self.refused(r"other\.nsp não é o Mario Kart 8 Deluxe", update=other)
        dlc = pfs(self.root / "dlc.nsp", [b"0100152000023001000000000000000a.tik", b"b.nca"])
        self.refused(r"dlc\.nsp é um DLC do MK8 Deluxe", update=dlc)

    def test_other_versions_and_missing_halves(self):
        old = pfs(self.root / "update-300.nsp", [PATCH_TIK, b"c.nca"])
        self.refused(r"update-300\.nsp é uma atualização de outra versão.*exatamente a atualização 4\.0\.1", update=old)
        changed = pfs(self.root / "base-trimmed.nsp", [BASE_TIK, b"d.nca"])
        self.refused(r"base-trimmed\.nsp é o MK8 Deluxe, mas o conteúdo do jogo base difere", base=changed)
        copy = pfs(self.root / "base-copy.nsz", [BASE_NCZ, BASE_TIK])
        self.refused("Os dois arquivos são o jogo base: falta a atualização 4.0.1", update=copy)
        self.refused("Os dois arquivos são a atualização 4.0.1: falta o jogo base", base=self.update,
                     update=pfs(self.root / "update-copy.nsz", [PATCH_NCZ, PATCH_TIK]))

    def test_incomplete_or_old_keys(self):
        self.keys.write_text("titlekek_09 = 00\n", encoding="utf-8")
        self.refused(r"falta header_key")
        self.keys.write_text("header_key = 00\ntitlekek_08 = 00\n", encoding="utf-8")
        self.refused(r"chaves da geração 9 usada pelo MK8 4\.0\.1")
        self.keys.write_text("header_key = 00\nmaster_key_09 = 00\n", encoding="utf-8")
        check_packages(self.base, self.update, self.keys)  # (the title key key can be derived from the master key)

    def test_missing_selection_is_named(self):
        missing = self.root / "missing.nsz"
        cases = [({"base_package": missing}, "Jogo base não selecionado"),
                 ({"update_package": missing}, "Atualização 4.0.1 não selecionada"),
                 ({"update_package": self.base}, "O mesmo arquivo foi escolhido"),
                 ({"prod_keys": missing}, "prod.keys não selecionado")]
        for change, message in cases:
            config = {"base_package": self.base, "update_package": self.update, "prod_keys": self.keys, **change}
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                prepare({k: str(v) for k, v in config.items()}, self.root / "local", lambda path: None)


if __name__ == "__main__":
    unittest.main()
