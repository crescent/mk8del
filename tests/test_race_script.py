"""Course selection precedes confirmation; acceleration lasts through the capture."""
import runpy
from pathlib import Path
import subprocess
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

script = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/test-race.py'))
race_input = script['race_input']
wait_capture = script['wait_capture']
courses_read = script['courses_read']
course_matches = script['course_matches']
COURSE_FOLDERS = script['COURSE_FOLDERS']
merge_hot_functions = script['merge_hot_functions']


class RaceScriptTests(unittest.TestCase):
    def test_course_selection_and_acceleration(self):
        for course, turns in [('stadium', 0), ('water-park', 1), ('sweet-sweet-canyon', 2)]:
            steps = [entry.split(':') for entry in race_input('time-trial', 6500, course).split(',')]
            self.assertEqual([int(s[0]) for s in steps], sorted(int(s[0]) for s in steps))
            rights = [s for s in steps if s[1] == 'RIGHT']
            self.assertEqual(len(rights), turns)
            # (between the cup's confirm at 1820 and the track's at 2000)
            self.assertTrue(all(1820 < int(s[0]) < 2000 for s in rights))
            self.assertIn(['1820', 'A', '6'], steps)
            self.assertIn(['2000', 'A', '6'], steps)
            self.assertEqual(steps[-1], ['2700', 'A', '3810'])

    def test_cup_moves_come_before_the_cup_is_confirmed(self):
        # Bell Cup: five columns right and one row down, then the fourth track.
        steps = [entry.split(':') for entry in race_input('time-trial', 4400, cup=11, track=3).split(',')]
        self.assertEqual([int(s[0]) for s in steps], sorted(int(s[0]) for s in steps))
        cup = [s[1] for s in steps if 1600 < int(s[0]) < 1820 and s[1] != 'A']
        self.assertEqual(cup, ['RIGHT'] * 5 + ['DOWN'])
        track = [s[1] for s in steps if 1820 < int(s[0]) < 2000]
        self.assertEqual(track, ['RIGHT'] * 3)

    def test_grand_prix_keeps_existing_navigation(self):
        script = race_input('grand-prix', 5200)
        self.assertNotIn('RIGHT', script)
        self.assertNotIn('880:DOWN', script)
        self.assertTrue(script.endswith('2700:A:2510'))

    def test_character_picker_moves_the_later_screens_one_press_later(self):
        plain = [entry.split(':') for entry in race_input('time-trial', 4400, cup=3, track=3).split(',')]
        late = [entry.split(':') for entry in race_input('time-trial', 4400, cup=3, track=3, picker=True).split(',')]
        self.assertEqual([int(s[0]) for s in late], sorted(int(s[0]) for s in late))
        self.assertEqual(late, [s for s in plain if int(s[0]) < 1260] + [['1260', 'A', '6']] +
                         [[str(int(s[0]) + 160), s[1], s[2]] for s in plain if int(s[0]) >= 1260][:-1] +
                         [['2860', 'A', '1550']])
        self.assertEqual(race_input('grand-prix', 5200, picker=True), race_input('grand-prix', 5200))

    def test_course_check_reads_the_raced_folder(self):
        log = '\n'.join(['[fs] read Course/Gu_Menu/course_model.szs', '[fs] read Course/Du_Animal_Summer/course_model.szs',
                         '[fs] read Course/Du_Animal_Summer/course.kcl', '[fs] read UI/cmn/menu/x.bntx'])
        self.assertEqual(courses_read(log), ['Du_Animal_Summer'])
        self.assertTrue(course_matches('Du_Animal_Summer', COURSE_FOLDERS[5][3]))
        self.assertFalse(course_matches('Gu_Airport', COURSE_FOLDERS[3][3]))
        self.assertEqual(len({name for cup in COURSE_FOLDERS for name in cup}), 48)

    def test_profile_merge_keeps_the_header_and_refuses_other_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'hot.txt'
            header = ['# runs', '# input main aa', '# input sdk bb', '# input subsdk0 cc']
            path.write_text('\n'.join(header + ['7000000024', '70000001b4']) + '\n', encoding='utf-8')
            modules = {'main': 'aa', 'sdk': 'bb', 'subsdk0': 'cc'}
            self.assertEqual(merge_hot_functions(path, '70000001b4 9\n700000019c 3\n', modules), 1)
            self.assertEqual(path.read_text(encoding='utf-8').splitlines(),
                             header + ['7000000024', '700000019c', '70000001b4'])
            self.assertEqual(merge_hot_functions(path, '700000019c 1\n7000000300 1\n', modules), 0)  # (once: setup)
            with self.assertRaisesRegex(SystemExit, 'other game modules'):
                merge_hot_functions(path, '7000000100 1\n', {**modules, 'main': 'dd'})

    def test_capture_monitor_preserves_exit_status(self):
        with subprocess.Popen([sys.executable, '-c', 'raise SystemExit(7)']) as child:
            with patch('shutil.disk_usage', return_value=SimpleNamespace(free=8 * 1024**3)):
                wait_capture(child, 10, 'test.log')
            self.assertEqual(child.returncode, 7)

    def test_capture_monitor_stops_owned_process_on_low_disk_or_timeout(self):
        for free, timeout, reason in [(3 * 1024**3, 10, '4 GiB'), (8 * 1024**3, -1, 'timed out')]:
            with self.subTest(reason=reason):
                with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as child:
                    with patch('shutil.disk_usage', return_value=SimpleNamespace(free=free)):
                        with self.assertRaisesRegex(SystemExit, reason):
                            wait_capture(child, timeout, 'test.log')
                    self.assertIsNotNone(child.poll())


if __name__ == '__main__':
    unittest.main()
