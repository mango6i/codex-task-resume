"""Build a portable, current-version Windows plugin ZIP using the standard library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ('.codex-plugin', '.mcp.json', 'hooks', 'server', 'skills', 'ui', 'assets',
           'tests', 'scripts', 'docs', 'README.md', 'README_EN.md', 'LICENSE', 'install.cmd', 'install.ps1', 'maintenance.ps1')
SKIP = {'__pycache__', '.pytest_cache', '.git', '.github', 'dist', 'build',
        'state.json', 'preferences.json', '验证记录.md'}


def checked(path):
    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ValueError('Linked release input refused: '+str(path))
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError('Release input escaped the project: '+str(path))
    return path


def walk(path):
    checked(path)
    if path.name in SKIP or path.name.startswith('.env') or path.suffix in {'.pyc', '.pyo', '.log', '.tmp'}:
        return
    if path.is_dir():
        for child in sorted(path.iterdir()):
            yield from walk(child)
    elif path.is_file():
        yield path


def build(output):
    manifest = json.loads((ROOT/'.codex-plugin/plugin.json').read_text(encoding='utf-8-sig'))
    if manifest['name'] != 'skillhub-quota-resume' or manifest['version'] != '1.0.0':
        raise ValueError('Unexpected plugin identity or version.')
    if manifest['interface']['displayName'] != 'Codex 任务续接' or manifest['author']['name'] != '果果':
        raise ValueError('Public product metadata does not match this release.')
    for relative in (manifest['skills'], manifest['hooks'], manifest['mcpServers'],
                     manifest['interface']['logo'], *manifest['interface']['screenshots']):
        checked(ROOT/relative)
    hooks = json.loads((ROOT/'hooks/hooks.json').read_text(encoding='utf-8-sig'))
    for groups in hooks['hooks'].values():
        for group in groups:
            for handler in group['hooks']:
                if handler.get('commandWindows') != 'python "$env:PLUGIN_ROOT/skills/quota-resume/scripts/hook.py"':
                    raise ValueError('Source hooks must be portable; use install.cmd to adapt them locally.')
    files = sorted(p for entry in ENTRIES for p in walk(ROOT/entry))
    output = output.absolute()
    cursor = output
    while cursor != cursor.parent:
        if cursor.exists() and (cursor.is_symlink() or getattr(cursor.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise ValueError('Linked output path refused: '+str(cursor))
        cursor = cursor.parent
    output.mkdir(parents=True, exist_ok=True)
    target = output/'codex-task-resume-1.0.zip'
    fd, temporary = tempfile.mkstemp(prefix='.task-resume-', suffix='.zip', dir=output)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, 'task-resume-1.0/'+path.relative_to(ROOT).as_posix())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Release CRC validation failed.')
            if len(archive.namelist()) != len(files):
                raise ValueError('Release file count does not match source.')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum = output/'SHA256SUMS.txt'
    checksum.write_text(digest+'  '+target.name+'\n', encoding='ascii')
    print(json.dumps({'archive': str(target), 'files': len(files), 'sha256': digest}, ensure_ascii=False))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    build(parser.parse_args().output)
