"""Drive an experimental candidate with scripted input and isolated saves."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from time import monotonic, sleep


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / 'local'
HOT_FUNCTIONS = ROOT / 'games/mk8d/profiles/4.0.1-hot-functions.txt'
PERF = re.compile(r'\[vk\] t=([\d.]+) frame (\d+) ([\d.]+) fps max (\d+)ms')


# The 4.0.1 cup screen in Time Trial: two rows of six cups, then the cup's four tracks in a row.
CUPS = ('mushroom', 'flower', 'star', 'special', 'egg', 'crossing',
        'shell', 'banana', 'leaf', 'lightning', 'triforce', 'bell')
COURSES = ('stadium', 'water-park', 'sweet-sweet-canyon')
# RomFS Course/ folder of each cup's tracks, in the order above; Animal Crossing has one folder per season.
COURSE_FOLDERS = (
    ('Gu_FirstCircuit', 'Gu_WaterPark', 'Gu_Cake', 'Gu_DossunIseki'),
    ('Gu_MarioCircuit', 'Gu_City', 'Gu_HorrorHouse', 'Gu_Expert'),
    ('Gu_Airport', 'Gu_Ocean', 'Gu_Techno', 'Gu_SnowMountain'),
    ('Gu_Cloud', 'Gu_Desert', 'Gu_BowserCastle', 'Gu_RainbowRoad'),
    ('Dgc_YoshiCircuit', 'Du_ExciteBike', 'Du_DragonRoad', 'Du_MuteCity'),
    ('Dgc_BabyPark', 'Dagb_CheeseLand', 'Du_Woods', 'Du_Animal_'),
    ('Gwii_MooMooMeadows', 'Gagb_MarioCircuit', 'Gds_PukupukuBeach', 'G64_KinopioHighway'),
    ('Ggc_DryDryDesert', 'Gsfc_DonutsPlain3', 'G64_PeachCircuit', 'G3ds_DKJungle'),
    ('Gds_WarioStadium', 'Ggc_SherbetLand', 'G3ds_MusicPark', 'G64_YoshiValley'),
    ('Gds_TickTockClock', 'G3ds_PackunSlider', 'Gwii_GrumbleVolcano', 'G64_RainbowRoad'),
    ('Dwii_WariosMine', 'Dsfc_RainbowRoad', 'Du_IcePark', 'Du_Hyrule'),
    ('D3ds_NeoBowserCity', 'Dagb_RibbonRoad', 'Du_Metro', 'Du_BigBlue'),
)
COURSE_READ = re.compile(r'\[fs\] read /?Course/([^/\s]+)/')


def courses_read(log):
    """Race courses whose files the game read (LARECOMP_LOG_FILES), without the menu's."""
    return sorted({name for name in COURSE_READ.findall(log) if name != 'Gu_Menu'})


def course_matches(folder, expected):
    return folder == expected or expected.endswith('_') and folder.startswith(expected)


def race_input(mode, last_frame, course='stadium', cup=0, track=None, picker=False):
    start = '400:A:6,520:DOWN:6,580:A:6,760:A:6'
    if mode == 'time-trial':
        track = COURSES.index(course) if track is None else track
        start += ',880:DOWN:6'
        for frame in range(940, 2401, 160):
            if frame == 1740:  # the cup screen: column, then row (the cursor starts on Mushroom Cup)
                # (the screen takes input from ~1680 on, a few frames later in some runs: a press there was lost and the
                # Egg Cup run raced Cloudtop Cruise; the moves start later and the confirm waits for them)
                moves = ['RIGHT'] * (cup % 6) + ['DOWN'] * (cup // 6)
                for step, button in enumerate(moves):
                    start += f',{1700 + step * 16}:{button}:6'
                frame = 1820
            elif frame == 1900:
                for step in range(track):
                    start += f',{1890 + step * 28}:RIGHT:6'
                frame = 2000
            start += f',{frame}:A:6'
    else:
        start += ''.join(f',{frame}:A:6' for frame in range(920, 2401, 160))
    script = start + f',2700:A:{max(last_frame - 2690, 10)}'
    if not picker or mode != 'time-trial':  # (Grand Prix only presses A: it never waits on a given screen)
        return script
    # A character with colours (Yoshi, Shy Guy...) opens a picker that takes one more A at 1260: every later screen
    # opens one A interval (160 frames) later. Which character the cursor starts on depends on the save.
    steps = [entry.split(':') for entry in script.split(',')]
    late = [f'{int(s) + 160 if int(s) >= 1260 else s}:{button}:{length}' for s, button, length in steps]
    late[-1] = f'2860:A:{max(last_frame - 2850, 10)}'
    return ','.join(sorted(late + ['1260:A:6'], key=lambda entry: int(entry.split(':')[0])))


def merge_hot_functions(path, profile, modules):
    """Add a run's profiled functions ("addr count" lines) to the hot list; returns how many were new.

    Functions entered only once are left out: setup code that runs straight through (the largest generated units are
    such functions, each 50-80 s to optimise) gains nothing from optimisation. The list's '# input' lines name the
    module hashes it was recorded from (toolchain/native.py ignores a list that doesn't match the build); a run of
    other modules is refused instead of mixing addresses.
    """
    header, hot = [], set()
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.startswith('#'):
            header.append(line)
        elif line:
            hot.add(int(line, 16))
    recorded = dict(line.split()[2:] for line in header if line.startswith('# input '))
    if recorded != modules:
        raise SystemExit(f'{path} was recorded from other game modules; record a new list for this build')
    rows = [line.split() for line in profile.splitlines() if line.strip()]
    new = {int(addr, 16) for addr, calls in rows if int(calls) >= 2} - hot
    if new:
        lines = header + [f'{addr:x}' for addr in sorted(hot | new)]
        Path(path).write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
    return len(new)


def wait_capture(child, timeout, log):
    """Monitor only this test's process; the runtime parent waits for its ASLR child."""
    started = monotonic()
    try:
        while child.poll() is None:
            if shutil.disk_usage(LOCAL).free < 4 * 1024**3:
                raise SystemExit(f'Capture stopped to preserve the 4 GiB disk reserve. See {log}')
            if monotonic() - started > timeout:
                raise SystemExit(f'Capture timed out after {timeout}s. See {log}')
            sleep(.5)
    finally:
        if child.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if child.poll() is None:
                child.kill()
            child.wait(timeout=15)


def run(args):
    if shutil.disk_usage(LOCAL).free < 4 * 1024**3:
        raise SystemExit('Race capture requires at least 4 GiB free on the local drive')
    # Saves, settings and the Mii profile come from the candidate's own data folder.
    origin = args.candidate.resolve().parent if args.candidate else LOCAL
    candidate = json.loads((args.candidate or LOCAL / 'candidate.json').read_text(encoding='utf-8'))
    for entry in [candidate['executable'], *candidate['files'].values()]:
        path = Path(entry['path'])
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != entry['sha256']:
            raise ValueError(f'Candidate changed on disk: {path}')

    session = Path(tempfile.mkdtemp(prefix=f'race-{args.mode}-', dir=LOCAL))
    print(f'Test output: {session}', flush=True)
    data = session / 'data'
    source = origin / 'user-no-mii'
    shutil.copytree(source / 'save', data / 'save')
    ini = (source / 'mk8d.ini').read_text(encoding='utf-8')
    ini = re.sub(r'^frame_gen\s*=.*$', 'frame_gen = off', ini, flags=re.M)
    (data / 'mk8d.ini').write_text(ini, encoding='utf-8')
    if args.cache_from:
        shutil.copytree(args.cache_from, data / 'cache')
    profile = session / 'mii-profile.json'
    shutil.copyfile(origin / 'mii-profile.json', profile)

    script = race_input(args.mode, args.last_frame, args.course, CUPS.index(args.cup), args.track,
                        args.character_picker)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('MK8_', 'LARECOMP_', 'RECOMP_', 'NX_LAN_'))}
    env.update(dict(args.env))
    # The runtime writes shader sources and debug-frame pass images under build/ relative to its data root.
    if any(key in env for key in ('LARECOMP_SAVE_MSL', 'LARECOMP_DEBUG_FRAME')):
        (data / 'build').mkdir()
    env.update(
        MK8_EXPERIMENTAL_NO_MII='1', MK8_MII_PROFILE_PATH=str(profile),
        LARECOMP_DATA_ROOT=str(data), LARECOMP_GAME_LIB=candidate['files']['gamelib']['path'],
        LARECOMP_INPUT=args.input or script,
        LARECOMP_WINDOW_SHOT=f'{args.last_frame - args.shot_step * (args.shots - 1)}:{args.last_frame}:{args.shot_step}',
        LARECOMP_SHOT_PATH=(session / 'frame-%05d.png').as_posix(),
        LARECOMP_EXIT_AFTER_SHOT='1', LARECOMP_LOG_FILES='1',
    )
    if args.fnprof:  # (every guest function entered from the start: runtime fnprof.c; written just before the exit at
        # the last capture, which comes first on the same frame)
        env.update(LARECOMP_FNPROF=f'0:{args.last_frame - 10}', LARECOMP_FNPROF_OUT=str(session / 'fnprof.txt'))
    (session / 'run.json').write_text(json.dumps({
        'candidate': candidate['identity'], 'mode': args.mode, 'options': args.env,
        'course': args.course, 'cup': args.cup, 'track': args.track, 'character_picker': args.character_picker,
        'input': env['LARECOMP_INPUT'], 'last_frame': args.last_frame,
        'cache_from': str(args.cache_from.resolve()) if args.cache_from else None,
    }, indent=2), encoding='utf-8')
    started = monotonic()
    log = session / 'game.log'
    with log.open('wb') as stream:
        child = subprocess.Popen(
            [candidate['executable']['path'], str(Path(candidate['data_root']) / 'extracted')],
            cwd=data, env=env, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT,
        )
        wait_capture(child, args.timeout, log)
    text = log.read_text(encoding='utf-8', errors='replace')
    rows = PERF.findall(text)
    raced = courses_read(text)
    print(f'Course: {", ".join(raced) or "none read"}', flush=True)
    print(f'Exit {child.returncode}, {monotonic() - started:.1f}s', flush=True)
    for elapsed, frame, fps, peak in rows[-8:]:
        print(f'{elapsed}s | frame {frame} | {fps} FPS | max {peak} ms')
    if child.returncode or not (session / f'frame-{args.last_frame:05d}.png').is_file():
        raise SystemExit('Candidate failed to reach the final capture; inspect game.log')
    if args.mode == 'time-trial' and not args.input:
        cup = CUPS.index(args.cup)
        track = COURSES.index(args.course) if args.track is None else args.track
        expected = COURSE_FOLDERS[cup][track]
        if len(raced) != 1 or not course_matches(raced[0], expected):
            raise SystemExit(f'Raced {raced or "no course"}, expected {expected}: the menu input missed; discard this run'
                             ' (a save whose character has colours needs --character-picker, one without must not use it)')
    if args.fnprof:
        modules = {name: candidate['files'][name]['sha256'] for name in ('main', 'sdk', 'subsdk0')}
        added = merge_hot_functions(HOT_FUNCTIONS, (session / 'fnprof.txt').read_text(encoding='utf-8'), modules)
        print(f'Hot functions: {added} new, added to {HOT_FUNCTIONS.relative_to(ROOT)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['grand-prix', 'time-trial'])
    parser.add_argument('--last-frame', type=int, default=3400)
    parser.add_argument('--shots', type=int, default=2)
    parser.add_argument('--shot-step', type=int, default=400)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--input', help='Override frame:button:duration input sequence')
    parser.add_argument('--cache-from', type=Path, help='Copy a previous cache directory; saves remain isolated')
    parser.add_argument('--course', choices=['stadium', 'water-park', 'sweet-sweet-canyon'], default='stadium',
                        help='Time Trial course in Mushroom Cup')
    parser.add_argument('--cup', choices=CUPS, default='mushroom', help='Time Trial cup (with --track)')
    parser.add_argument('--track', type=int, choices=range(4), help='Track 0-3 of --cup (overrides --course)')
    parser.add_argument('--character-picker', action='store_true',
                        help="Time Trial: the save's character opens a colour picker (Yoshi, Shy Guy...); one more A")
    parser.add_argument('--fnprof', action='store_true',
                        help='Profile the functions entered and add them to the hot list (games/mk8d/profiles)')
    parser.add_argument('--env', action='append', default=[], type=lambda x: x.split('=', 1))
    parser.add_argument('--candidate', type=Path, help='candidate.json of another data folder (e.g. a fresh build)')
    args = parser.parse_args()
    if args.mode != 'time-trial' and (args.course != 'stadium' or args.cup != 'mushroom' or args.track is not None):
        parser.error('--course, --cup and --track require time-trial')
    if (args.shots < 1 or args.shot_step < 1 or args.last_frame <= args.shot_step * (args.shots - 1)
            or any(len(pair) != 2 for pair in args.env)):
        parser.error('Require positive capture frames, capture count and step, and --env NAME=VALUE')
    run(args)
