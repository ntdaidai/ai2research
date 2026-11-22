"""Run the installed draw.io desktop CLI on a short-lived private X display."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile

home = Path.home()
reader, writer = os.pipe()
xvfb = subprocess.Popen([str(home / '.local/opt/xvfb/usr/bin/Xvfb'), '-displayfd', str(writer),
                         '-nolisten', 'tcp', '-screen', '0', '1280x800x24'],
                        pass_fds=(writer,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.close(writer)
try:
    if not select.select([reader], [], [], 15)[0]:
        raise RuntimeError('Xvfb did not start within 15 seconds')
    display = os.read(reader, 64).decode().strip()
    if not display.isdigit():
        raise RuntimeError('Xvfb failed to allocate a display')
    env = dict(os.environ, DISPLAY=':' + display)
    # Parallel exporters must not share Electron's mutable profile/GPU cache.
    temporary_root = Path(__file__).resolve().parents[1] / '.local/tmp/drawio'
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='export-', dir=temporary_root) as profile:
        result = subprocess.run([str(home / '.local/opt/drawio/opt/drawio/drawio'),
                                 '--no-sandbox', '--disable-gpu', '--user-data-dir='+profile,
                                 *sys.argv[1:]], env=env, timeout=100)
    sys.exit(result.returncode)
finally:
    os.close(reader)
    xvfb.terminate()
    try:
        xvfb.wait(timeout=5)
    except subprocess.TimeoutExpired:
        xvfb.kill()
        xvfb.wait()
