"""The Online Play notice and the split-screen diagnostics apply to the pinned upstream and refuse other versions."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import prepare_host_sources
import online_notice
import split_screen


class ModePatchTests(unittest.TestCase):
    def test_prepared_runtime_has_the_mk8_notice_and_pad_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', Path(folder))
            notice = (runtime/'ui/online_rows.mm').read_text(encoding='utf-8')
            self.assertIn('join a room in the "\n                 "MK8-online launcher', notice)
            self.assertIn('choose Nextendo and restart the game', notice)
            self.assertNotIn('sign in to Nextendo Network.", menu);', notice)
            # (the signed-in branches of upstream stay as they are)
            self.assertIn("You're signed in to Nextendo", notice)
            source = (runtime/'hle/input.c').read_text(encoding='utf-8')
            self.assertIn('MK8_TRACE_PADS', source)
            self.assertEqual(source.count('mk8_pad_kind = '), 6)
            self.assertIn('MK8_TRACE_IMPORTS', (runtime/'loader.c').read_text(encoding='utf-8'))

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'ui').mkdir()
            (runtime/'hle').mkdir()
            (runtime/'ui/online_rows.mm').write_text('int x;\n')
            (runtime/'hle/input.c').write_text('int y;\n')
            with self.assertRaises(ValueError):
                online_notice.prepare(runtime)
            with self.assertRaises(ValueError):
                split_screen.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
