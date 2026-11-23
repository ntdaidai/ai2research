"""Install isolated project environments; no agent settings or research calls."""
import argparse
import os
from pathlib import Path
import subprocess

PYTHON_VERSION = "3.14.4"
ARXIV_VERSION = "3.12.2"


def environment_python(directory, *, windows=None):
    windows = os.name == "nt" if windows is None else windows
    return Path(directory) / ("Scripts/python.exe" if windows else "bin/python")


def bootstrap(root, uv, *, workbench_only=False, skip_arxiv=False):
    root = Path(root).resolve()
    env = dict(os.environ, PYTHONUTF8="1", UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(root / ".local/uv-cache")),
               UV_PYTHON_INSTALL_DIR=os.environ.get("UV_PYTHON_INSTALL_DIR", str(root / ".local/python")))

    def run(argv):
        subprocess.run([str(x) for x in argv], cwd=root, env=env, check=True)

    def install(directory, version, lockfile=None):
        python = environment_python(root / directory)
        if not python.is_file():
            run([uv, "python", "install", "--no-bin", "--no-registry", version])
            run([uv, "venv", "--python", version, root / directory])
        actual = subprocess.check_output([str(python), "--version"], env=env, text=True).strip()
        if actual != "Python " + version:
            raise ValueError(f"{directory} expects Python {version}; move this old environment aside and retry")
        if lockfile:
            run([uv, "pip", "sync", "--python", python, root / lockfile])
            run([uv, "pip", "check", "--python", python])
        return python

    python = install(".venv", PYTHON_VERSION, None if workbench_only else "requirements-harness.lock.txt")
    if not workbench_only and not skip_arxiv:
        install(".venv-arxiv", ARXIV_VERSION, "requirements-arxiv.lock.txt")
    arguments = [python, root / "tools/verify_install.py"]
    if not workbench_only:
        arguments.append("--full")
    run(arguments)
    print("Installation complete. Run 2_start.cmd on Windows, or python tools/run_workbench.py.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--workbench-only", action="store_true")
    parser.add_argument("--skip-arxiv", action="store_true")
    args = parser.parse_args()
    try:
        bootstrap(Path(__file__).resolve().parents[1], args.uv, workbench_only=args.workbench_only, skip_arxiv=args.skip_arxiv)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Installation failed: {exc}\nFix the reported issue and run the installer again.\n")


if __name__ == "__main__":
    main()
