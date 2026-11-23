"""Build a clean, relocatable source release; no Git checkout is required."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '.gitattributes', '.gitignore', 'AGENTS.md', 'HANDOFF.md', 'README.md', 'RESEARCH_PLAN.md',
    'ai2research', 'ai2research.cmd', '1_install.cmd', '2_start.cmd', '3_connect_codex.cmd', '4_check.cmd',
    'harness.toml', 'project.json', 'requirements-harness.txt', 'requirements-harness.lock.txt',
    'requirements-arxiv.lock.txt', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CAPABILITIES.md',
    'VALIDATION.md', 'MIGRATION.md', 'WINDOWS_QUICKSTART.md', 'LINUX_QUICKSTART.md', 'START_HERE.txt',
}
REQUIRED = {'README.md', 'harness.toml', 'LICENSE', '1_install.cmd', '2_start.cmd',
            'tools/run_workbench.py', 'tools/bootstrap_windows.ps1', 'web/showcase/catalog.js'}
SOURCE_SUFFIXES = {
    'tools': {'.py', '.sh', '.ps1'}, 'tests': {'.py', '.mjs'},
    'protocol': {'.md'}, 'templates': {'.md', '.tex', '.drawio'}, 'workflow': {'.json'},
    'skills': {'.md', '.yaml'}, 'web': {'.html', '.css', '.js', '.svg', '.md'},
    'docs': {'.md', '.html', '.css', '.js', '.svg', '.json'},
}
RESEARCH_FILES = {'research/FRAMEWORK_GUIDE.md', 'research/README.md'}


def collect_sources(root):
    root = Path(root).resolve()
    files = []
    names = set(ROOT_FILES) | RESEARCH_FILES | {'docs/.nojekyll'}
    for directory, suffixes in SOURCE_SUFFIXES.items():
        base = root / directory
        if base.is_symlink():
            raise ValueError(f'Linked source directory: {directory}')
        if not base.is_dir():
            continue
        for current, directories, filenames in os.walk(base, followlinks=False):
            directories[:] = [name for name in directories if not name.startswith('.') and name != '__pycache__']
            for name in directories:
                if (Path(current) / name).is_symlink():
                    raise ValueError('Linked source directory: ' + str(Path(current) / name))
            for name in filenames:
                path = Path(current) / name
                if not name.startswith('.') and path.suffix in suffixes:
                    names.add(path.relative_to(root).as_posix())
    for name in sorted(names):
        source = root / name
        if not source.exists() and not source.is_symlink():
            continue
        if source.is_symlink() or source.resolve() != source or not source.is_file():
            raise ValueError(f'Expected a regular source file: {name}')
        files.append((name, source.read_bytes()))
    missing = REQUIRED - {name for name, _ in files}
    if missing:
        raise ValueError('Missing release sources: ' + ', '.join(sorted(missing)))
    return files


def package(root, target, prefix='ai2research'):
    target = Path(target).resolve()
    if '/' in prefix or '\\' in prefix or prefix in {'', '.', '..'}:
        raise ValueError('Release prefix must be one directory name')
    if target.exists():
        raise ValueError('Select a new release filename; existing archives are preserved')
    files = collect_sources(root)
    manifest = {'schema_version': 1, 'kind': 'clean_source_release', 'private_state_included': False,
                'files': [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in files]}
    files.append(('RELEASE_MANIFEST.json', (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode('utf-8')))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.partial')
    try:
        if target.suffix == '.zip':
            with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for name, raw in files:
                    item = zipfile.ZipInfo(prefix + '/' + name)
                    item.compress_type = zipfile.ZIP_DEFLATED
                    item.external_attr = (0o755 if name == 'ai2research' or name.endswith('.sh') else 0o644) << 16
                    archive.writestr(item, raw)
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None:
                    raise ValueError('Archive CRC check failed')
        elif target.name.endswith('.tar.gz'):
            with tarfile.open(temporary, 'x:gz') as archive:
                for name, raw in files:
                    item = tarfile.TarInfo(prefix + '/' + name)
                    item.size = len(raw)
                    item.mode = 0o755 if name == 'ai2research' or name.endswith('.sh') else 0o644
                    archive.addfile(item, io.BytesIO(raw))
        else:
            raise ValueError('Use a .zip or .tar.gz release filename')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {'archive': str(target), 'sha256': digest, 'files': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(package(ROOT, args.archive), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
