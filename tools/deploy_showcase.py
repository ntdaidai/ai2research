"""Install a user-owned, restartable systemd service for the read-only showcase."""
import argparse
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--name", default="ai2research-showcase")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--static-directory", type=Path, help="Serve exported frontend files without the research backend")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.name) or not re.fullmatch(r"[A-Za-z0-9.:-]+", args.host):
        parser.error("Use a plain service name and host address")
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    root = Path(__file__).resolve().parents[1]
    python = args.python or root / ".venv/bin/python"
    python = python.absolute()
    if not python.is_file():
        parser.error("Create the project's Python environment first")
    if any(c in str(root) + str(python) for c in ('\n', '\r', '"', '%')):
        parser.error("Unsupported characters in service path")
    command = f'"{python}" "{root / "tools/harness_cli.py"}" showcase --host {args.host} --port {args.port}'
    if args.static_directory:
        directory = args.static_directory.resolve()
        if not (directory / "index.html").is_file() or any(c in str(directory) for c in ('\n', '\r', '"', '%')):
            parser.error("Select an exported static frontend directory")
        command = f'"{python}" -m http.server {args.port} --bind {args.host} --directory "{directory}"'
    subprocess.run(["systemctl", "--user", "show", "-p", "Version"], check=True, stdout=subprocess.DEVNULL)
    folder = Path.home() / ".config/systemd/user"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (args.name + ".service")
    marker = "# Managed by ai2research tools/deploy_showcase.py"
    if path.exists() and not path.read_text().startswith(marker):
        raise SystemExit("Existing unrelated service preserved: " + str(path))
    path.write_text(f'''{marker}
[Unit]
Description=ai2research read-only research showcase
After=network.target

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={command}
Environment=AI2RESEARCH_TRANSPORT=local
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
''')
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", args.name + ".service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", args.name + ".service"], check=True)
    print(f"http://{args.host}:{args.port}/")
    print(f"Status: systemctl --user status {args.name}")
    print(f"Stop: systemctl --user disable --now {args.name}")


if __name__ == "__main__":
    main()
