"""Time Trial on every track of MK8 Deluxe 4.0.1: race captures, frame rate and log problems per track.

    python scripts/test-tracks.py --out local/track-battery           # all 48 (resumable: done tracks are skipped)
    python scripts/test-tracks.py --out local/track-battery --cups egg crossing
    python scripts/test-tracks.py --out local/track-battery --sheet   # only rebuild results.md and contact sheets

Each track runs in its own isolated session (scripts/test-race.py) with the same scripted input: the kart only
accelerates, so the captures are views of the track, not a qualified race. The result per track lands in
`<out>/results.json`; `<out>/results.md` and `<out>/sheet-<cup>.png` are rebuilt from it for review.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CUPS = {
    'mushroom': ('Mario Kart Stadium', 'Water Park', 'Sweet Sweet Canyon', 'Thwomp Ruins'),
    'flower': ('Mario Circuit', 'Toad Harbor', 'Twisted Mansion', 'Shy Guy Falls'),
    'star': ('Sunshine Airport', 'Dolphin Shoals', 'Electrodrome', 'Mount Wario'),
    'special': ('Cloudtop Cruise', 'Bone-Dry Dunes', "Bowser's Castle", 'Rainbow Road'),
    'egg': ('GCN Yoshi Circuit', 'Excitebike Arena', 'Dragon Driftway', 'Mute City'),
    'crossing': ('GCN Baby Park', 'GBA Cheese Land', 'Wild Woods', 'Animal Crossing'),
    'shell': ('Wii Moo Moo Meadows', 'GBA Mario Circuit', 'DS Cheep Cheep Beach', "N64 Toad's Turnpike"),
    'banana': ('GCN Dry Dry Desert', 'SNES Donut Plains 3', 'N64 Royal Raceway', '3DS DK Jungle'),
    'leaf': ('DS Wario Stadium', 'GCN Sherbet Land', '3DS Music Park', 'N64 Yoshi Valley'),
    'lightning': ('DS Tick-Tock Clock', '3DS Piranha Plant Slide', 'Wii Grumble Volcano', 'N64 Rainbow Road'),
    'triforce': ("Wii Wario's Gold Mine", 'SNES Rainbow Road', 'Ice Ice Outpost', 'Hyrule Circuit'),
    'bell': ('3DS Neo Bowser City', 'GBA Ribbon Road', 'Super Bell Subway', 'Big Blue'),
}
PERF = re.compile(r'\[vk\] t=[\d.]+ frame (\d+) ([\d.]+) fps max (\d+)ms')
PROBLEMS = re.compile(r'type mismatch|not supported|view failed|image creation failed|\[nan\]|FATAL|unimplemented|'
                      r'skipped \(exec|c0000005|exception|\[mk8:bc\].*not size-compatible', re.I)
STATS = r'''
param([string]$Path)
Add-Type -AssemblyName System.Drawing
$bmp = [System.Drawing.Bitmap]::FromFile($Path)
$rect = New-Object System.Drawing.Rectangle 0, 0, $bmp.Width, $bmp.Height
$data = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$bytes = New-Object byte[] ($data.Stride * $bmp.Height)
[System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $bytes, 0, $bytes.Length)
$bmp.UnlockBits($data); $stride = $data.Stride; $h = $bmp.Height; $w = $bmp.Width; $bmp.Dispose()
$sum = 0.0; $n = 0; $black = 0; $white = 0; $magenta = 0
for ($y = 0; $y -lt $h; $y += 3) { for ($x = 0; $x -lt $w; $x += 3) {
  $i = $y * $stride + $x * 4; $b = $bytes[$i]; $g = $bytes[$i + 1]; $r = $bytes[$i + 2]
  $l = 0.0722 * $b + 0.7152 * $g + 0.2126 * $r; $sum += $l; $n++
  if ($l -lt 8) { $black++ }; if ($l -gt 250) { $white++ }; if ($r -gt 200 -and $b -gt 200 -and $g -lt 60) { $magenta++ } } }
"{0:F1} {1:F2} {2:F2} {3:F2}" -f ($sum / $n), (100.0 * $black / $n), (100.0 * $white / $n), (100.0 * $magenta / $n)
'''
SHEET = r'''
param([string]$Out, [string]$Title, [string]$List)
$Images = $List -split '\|'
Add-Type -AssemblyName System.Drawing
$w = 400; $h = 225; $cols = 3; $rows = [math]::Ceiling($Images.Count / $cols)
$sheet = New-Object System.Drawing.Bitmap ($w * $cols), ($h * $rows + 24)
$g = [System.Drawing.Graphics]::FromImage($sheet); $g.Clear([System.Drawing.Color]::Black)
$font = New-Object System.Drawing.Font 'Segoe UI', 10
$g.DrawString($Title, $font, [System.Drawing.Brushes]::White, 4, 3)
for ($k = 0; $k -lt $Images.Count; $k++) {
  $img = [System.Drawing.Image]::FromFile($Images[$k])
  $x = ($k % $cols) * $w; $y = [math]::Floor($k / $cols) * $h + 24
  $g.DrawImage($img, $x, $y, $w, $h); $img.Dispose()
  $g.DrawString([IO.Path]::GetFileNameWithoutExtension($Images[$k]), $font, [System.Drawing.Brushes]::Yellow, $x + 4, $y + 2) }
$sheet.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
'''


def powershell(script, *args):
    folder = Path(os.environ.get('TEMP', '.'))
    path = folder / f'mk8-tracks-{os.getpid()}.ps1'
    path.write_text(script, encoding='utf-8-sig')
    try:
        return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(path), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    finally:
        path.unlink(missing_ok=True)


def run_track(cup, index, args):
    command = [sys.executable, str(ROOT / 'scripts/test-race.py'), 'time-trial', '--cup', cup, '--track', str(index),
               '--last-frame', str(args.last_frame), '--shots', '3', '--shot-step', '500', '--timeout', '420',
               '--cache-from', str(args.cache)] + (['--character-picker'] if args.character_picker else [])
    for pair in args.env:
        command += ['--env', pair]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    session = re.search(r'Test output: (.+)', done.stdout)
    result = {'cup': cup, 'track': index, 'name': CUPS[cup][index], 'ok': done.returncode == 0,
              'session': session.group(1).strip() if session else None, 'error': done.stdout[-400:] if done.returncode else None}
    if result['session']:
        session = Path(result['session'])
        log = (session / 'game.log').read_text(encoding='utf-8', errors='replace') if (session / 'game.log').is_file() else ''
        rows = [(int(f), float(fps), int(peak)) for f, fps, peak in PERF.findall(log)]
        race = [row for row in rows if row[0] > 2900]
        result['fps_min'] = min((fps for _, fps, _ in race), default=None)
        result['fps_avg'] = round(sum(fps for _, fps, _ in race) / len(race), 1) if race else None
        result['peak_ms'] = max((peak for _, _, peak in race), default=None)
        problems = {}
        for line in log.splitlines():
            if PROBLEMS.search(line):
                key = re.sub(r'0x[0-9a-f]+|[0-9a-f]{8,}|\d+', '#', line.strip())[:140]
                problems[key] = problems.get(key, 0) + 1
        result['problems'] = sorted(problems.items(), key=lambda kv: -kv[1])[:8]
        result['shots'] = []
        for shot in sorted(session.glob('frame-*.png')):
            mean, black, white, magenta = map(float, powershell(STATS, str(shot)).replace(',', '.').split())
            result['shots'].append({'file': str(shot), 'mean': mean, 'black': black, 'white': white, 'magenta': magenta})
        # The session's save / shader cache copy (~650 MB) is temporary: kept are the log, the captures and run.json.
        data = session / 'data'
        if data.is_dir() and not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in data.rglob('*')):
            shutil.rmtree(data, ignore_errors=True)
    return result


def write_report(out, results):
    lines = ['# Bateria de pistas (Contra o Relógio, quadros 3400/3900/4400)', '',
             '| Copa | # | Pista | Resultado | FPS (mín/méd) | Pico | Luma | Preto % | Branco % | Magenta % | Problemas no log |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in sorted(results.values(), key=lambda r: (list(CUPS).index(r['cup']), r['track'])):
        shots = r.get('shots') or []
        fmt = lambda key: '/'.join(f"{s[key]:.0f}" if key == 'mean' else f"{s[key]:.1f}" for s in shots) or '—'
        flags = []
        if any(s['mean'] < 25 or s['black'] > 60 for s in shots):
            flags.append('**escuro/preto**')
        if any(s['white'] > 8 for s in shots):
            flags.append('**estourado**')
        if any(s['magenta'] > 0.5 for s in shots):
            flags.append('**magenta**')
        state = 'ok' if r['ok'] else 'falhou'
        problems = '; '.join(f'{n}× {k}' for k, n in (r.get('problems') or [])[:3]) or '—'
        lines.append(f"| {r['cup']} | {r['track'] + 1} | {r['name']} | {state} {' '.join(flags)} | "
                     f"{r.get('fps_min') or '—'}/{r.get('fps_avg') or '—'} | {r.get('peak_ms') or '—'} ms | {fmt('mean')} | "
                     f"{fmt('black')} | {fmt('white')} | {fmt('magenta')} | {problems} |")
    (out / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    for cup in CUPS:
        images = []
        for r in sorted((r for r in results.values() if r['cup'] == cup), key=lambda r: r['track']):
            for s in r.get('shots') or []:
                named = out / 'shots' / f"{cup}-{r['track'] + 1}-{Path(s['file']).stem[-5:]}.png"
                named.parent.mkdir(exist_ok=True)
                if Path(s['file']).is_file():
                    named.write_bytes(Path(s['file']).read_bytes())
                if named.is_file():
                    images.append(str(named))
        if images:
            powershell(SHEET, '-Out', str(out / f'sheet-{cup}.png'), '-Title', f'{cup}: ' + ' / '.join(CUPS[cup]), '-List', '|'.join(images))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cups', nargs='+', choices=list(CUPS), default=list(CUPS))
    parser.add_argument('--tracks', nargs='+', type=int, choices=range(4), default=[0, 1, 2, 3])
    parser.add_argument('--last-frame', type=int, default=4400)
    parser.add_argument('--character-picker', action='store_true', help='passed to test-race.py')
    parser.add_argument('--cache', type=Path, default=ROOT / 'local/user-no-mii/cache')
    parser.add_argument('--env', action='append', default=[], help='NAME=VALUE passed to every run')
    parser.add_argument('--again', action='store_true', help='run tracks already in results.json again')
    parser.add_argument('--sheet', action='store_true', help='only rebuild the report and contact sheets')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    store = out / 'results.json'
    results = json.loads(store.read_text(encoding='utf-8')) if store.is_file() else {}
    if not args.sheet:
        for cup in args.cups:
            for index in args.tracks:
                key = f'{cup}-{index}'
                if key in results and not args.again:
                    continue
                print(f'{key}: {CUPS[cup][index]}…', flush=True)
                results[key] = run_track(cup, index, args)
                r = results[key]
                print(f"  {'ok' if r['ok'] else 'FALHOU'} fps {r.get('fps_min')}/{r.get('fps_avg')} "
                      f"luma {[s['mean'] for s in r.get('shots', [])]} problemas {len(r.get('problems') or [])}", flush=True)
                store.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding='utf-8')
    write_report(out, results)
    print(out / 'results.md')


if __name__ == '__main__':
    main()
