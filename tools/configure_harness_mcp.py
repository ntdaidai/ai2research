"""Register this checkout's stdio service, preserving unrelated MCP settings."""
import argparse
import json
import os
from pathlib import Path
import tomllib


def configuration(root, *, windows=None):
    root = Path(root).resolve()
    windows = os.name == 'nt' if windows is None else windows
    python = root / ('.venv/Scripts/python.exe' if windows else '.venv/bin/python')
    settings = tomllib.loads((root / 'harness.toml').read_text(encoding='utf-8'))
    key_env = settings.get('review', {}).get('key_env', 'RESEARCH_REVIEW_API_KEY')
    return {'command': str(python), 'args': [str(root / 'tools/harness_mcp.py')],
            'cwd': str(root), 'env': {'PYTHONUTF8': '1'},
            'env_vars': sorted({key_env, 'DASHSCOPE_API_KEY', 'RESEARCH_STATE_DIR', 'DRAWIO_EXE', 'AI2RESEARCH_TRANSPORT'}),
            'startup_timeout_sec': 45, 'tool_timeout_sec': 120}


def register(root, target, *, name='ai2research', dry_run=False, windows=None):
    target = Path(target)
    original = target.read_text(encoding='utf-8') if target.exists() else ''
    parsed = tomllib.loads(original)
    entry = configuration(root, windows=windows)
    if not name.replace('_', '').replace('-', '').isalnum() or not name.isascii():
        raise ValueError("Use an ASCII MCP name containing letters, digits, '-' or '_'")
    existing = parsed.get('mcp_servers', {}).get(name)
    if existing is not None:
        if existing != entry:
            raise ValueError(f'{name} already exists with a different configuration; left unchanged')
        return {'status': 'already_registered', 'name': name}
    block = f'\n[mcp_servers.{name}]\n' + '\n'.join(
        key + ' = ' + json.dumps(value, ensure_ascii=True) for key, value in entry.items() if key != 'env')
    block += f'\n[mcp_servers.{name}.env]\nPYTHONUTF8 = "1"\n'
    tomllib.loads(original + block)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.ai2research.tmp')
        try:
            temporary.write_text(original + block, encoding='utf-8')
            if os.name == 'posix':
                temporary.chmod(target.stat().st_mode & 0o777 if target.exists() else 0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {'status': 'preview' if dry_run else 'registered', 'name': name, 'entry': entry}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='ai2research')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml')
    args = parser.parse_args()
    try:
        print(json.dumps(register(Path(__file__).resolve().parents[1], args.config, name=args.name, dry_run=args.dry_run), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
