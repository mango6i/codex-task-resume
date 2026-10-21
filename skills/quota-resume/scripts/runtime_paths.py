"""Locate an existing Codex CLI without changing the user's PATH."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def candidates():
    paths = []
    configured = os.environ.get('TASK_RESUME_CODEX')
    if configured:
        paths.append(Path(configured))
    command = shutil.which('codex')
    if command:
        paths.append(Path(command))
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')
    for folder, pattern in [(local/'OpenAI/Codex/bin', '*/codex.exe'),
                            (Path(os.environ.get('ProgramFiles') or 'C:/Program Files')/'WindowsApps',
                             'OpenAI.Codex_*/app/resources/codex.exe')]:
        try:
            paths.extend(sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True))
        except OSError:
            pass
    roaming = Path(os.environ.get('APPDATA') or Path.home()/'AppData/Roaming')
    paths.append(roaming/'npm/codex.cmd')
    seen = set()
    for path in paths:
        try:
            key = os.path.normcase(str(path.absolute()))
            if key not in seen and path.is_absolute() and path.is_file():
                seen.add(key)
                yield path
        except OSError:
            continue


def find_codex(check=False):
    deadline = time.monotonic()+10
    for path in candidates():
        if not check:
            return str(path)
        budget = min(3, deadline-time.monotonic())
        if budget <= 0:
            break
        try:
            result = subprocess.run([str(path), '--version'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=budget,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode == 0 and result.stdout.strip().startswith('codex-cli '):
                return str(path)
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise ValueError('未找到可用的 Codex CLI。请先启动一次 Codex 客户端，或安装 Codex CLI 后重试。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps({'path': find_codex(args.check)}, ensure_ascii=True))
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=True))
        raise SystemExit(2)
