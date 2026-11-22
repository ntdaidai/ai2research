#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$root"
umask 077
uv="${AI2RESEARCH_UV:-uv}"
sync_environment() {
    local directory="$1" version="$2" lockfile="$3"
    local python="$directory/bin/python"
    if [[ ! -x "$python" ]]; then
        "$uv" venv --python "$version" "$directory"
    fi
    if [[ "$("$python" --version)" != "Python $version" ]]; then
        printf '%s expects Python %s. Move the existing environment before retrying.\n' "$directory" "$version" >&2
        return 1
    fi
    "$uv" pip sync --python "$python" "$lockfile"
    "$uv" pip check --python "$python"
}
sync_environment .venv 3.14.4 requirements-harness.lock.txt
sync_environment .venv-arxiv 3.12.2 requirements-arxiv.lock.txt
if [[ "${AI2RESEARCH_INSTALL_SKILLS:-0}" == "1" ]]; then
    "$root/.venv/bin/python" "$root/tools/install_local_skill.py"
fi
bash ai2research doctor
