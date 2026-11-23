"""Run the workbench with Python's standard library only."""
import argparse
from pathlib import Path
import sys
import threading
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    from harness.showcase import make_server
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        server = make_server(args.host, args.port)
    except OSError as exc:
        parser.exit(1, f"Cannot open port {args.port}: {exc}. Try --port 8766.\n")
    url = f"http://{args.host}:{server.server_port}/"
    print(f"ai2research: {url}\nKeep this window open. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        timer = threading.Timer(.3, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
