"""Export the research dashboard and verified outputs for backend-free hosting."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness.showcase import ROOT, WEB, artifact_bytes, snapshot, state_directory


def export(output: Path):
    output = output.resolve()
    if output == ROOT or ROOT.is_relative_to(output) or output == WEB:
        raise ValueError("Select a dedicated static output directory")
    output.mkdir(parents=True, exist_ok=True)
    assets = ("index.html", "style.css", "catalog.js", "app.js", "favicon.svg")
    for name in assets:
        if (output / name).is_symlink():
            raise ValueError("Static output files must not be symbolic links")
    data = snapshot()
    projects = data["projects"]
    # Retain independent project snapshots so the selector continues to work.
    project_files = {}
    for item in projects:
        value = data if data.get("project", {}).get("id") == item["id"] else snapshot(item["id"])
        project = value["project"]
        for artifact in project["outputs"]:
            raw, name, _mime = artifact_bytes(state_directory(), project["id"], artifact["artifact_id"])
            relative = Path("files") / project["id"] / (artifact["artifact_id"] + Path(name).suffix)
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                raise ValueError("Static outputs must not be symbolic links")
            destination.write_bytes(raw)
            artifact["url"] = "./" + relative.as_posix()
        value["mode"] = "static_snapshot"
        value["exported_at"] = datetime.now(timezone.utc).isoformat()
        project_files[item["id"]] = value
    data["mode"] = "static_snapshot"
    data["exported_at"] = datetime.now(timezone.utc).isoformat()
    payload = {"default_project": data["project"]["id"] if data.get("project") else None,
               "snapshots": project_files, "empty": data}
    for name in assets:
        shutil.copyfile(WEB / name, output / name)
    html = (output / "index.html").read_text()
    html = html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-static="true">')
    html = html.replace('href="/style.css"', 'href="./style.css"').replace('src="/app.js"', 'src="./app.js"').replace('src="/catalog.js"', 'src="./catalog.js"').replace('href="/favicon.svg"', 'href="./favicon.svg"').replace('href="/"', 'href="./"')
    html = html.replace('真实研究记录 · 自动同步 · 随时回看', '真实研究快照 · 静态部署 · 随时回看')
    html = html.replace('页面可见时每30秒同步，也可以立即刷新。', '当前页面是研究快照，重新导出静态文件后刷新即可更新。')
    (output / "index.html").write_text(html)
    script = (output / "app.js").read_text()
    script = script.replace('fetch(`/api/snapshot${query}`,', 'fetch(document.documentElement.dataset.static === "true" ? "./snapshot.json" : `/api/snapshot${query}`,')
    script = script.replace('const data = await response.json();', '''let data = await response.json();
    if (document.documentElement.dataset.static === "true") {
      data = data.snapshots[requested || data.default_project] || data.empty;
    }''')
    script = script.replace('`本地记录 · ${date(data.generated_at)} · 每30秒同步`', '`静态快照 · ${date(data.exported_at || data.generated_at)} · 重新导出后更新`')
    (output / "app.js").write_text(script)
    destination = output / "snapshot.json"
    temporary = output / "snapshot.next.json"
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n")
    temporary.replace(destination)
    print(f"Static research dashboard: {output} ({len(project_files)} projects)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/showcase-static")
    export(parser.parse_args().output)
