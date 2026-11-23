"""Build a public, empty workbench catalog without reading research state."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


def build():
    from harness.showcase_catalog import tool_inventory
    output = ROOT / 'docs'
    output.mkdir(exist_ok=True)
    for name in ('index.html', 'style.css', 'catalog.js', 'app.js', 'favicon.svg'):
        shutil.copyfile(ROOT / 'web/showcase' / name, output / name)
    html = (output / 'index.html').read_text(encoding='utf-8')
    html = html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-demo="true">')
    for source, target in [('href="/style.css"','href="./style.css"'),('src="/app.js"','src="./app.js"'),('src="/catalog.js"','src="./catalog.js"'),('href="/favicon.svg"','href="./favicon.svg"'),('href="/"','href="./"')]:
        html = html.replace(source, target)
    (output / 'index.html').write_text(html, encoding='utf-8')
    script = (output / 'app.js').read_text(encoding='utf-8')
    script = script.replace('fetch(`/api/snapshot${query}`,', 'fetch(document.documentElement.dataset.demo === "true" ? "./demo-data.json" : `/api/snapshot${query}`,')
    script = script.replace('`本地记录 · ${date(data.generated_at)} · 每30秒同步`', '`公共工具目录 · 项目记录为空`')
    (output / 'app.js').write_text(script, encoding='utf-8')
    with tempfile.TemporaryDirectory() as temporary:
        inventory = tool_inventory(root=ROOT, home=temporary, codex_home=temporary, settings={})
    # Ship names/descriptions/sources only; no account, runtime or review configuration.
    items = [{key:item[key] for key in ('id','kind','title','description','links')} for item in inventory['items']]
    snapshot = {'schema_version':1, 'generated_at':datetime.now(timezone.utc).isoformat(),
                'mode':'public_catalog', 'projects':[], 'project':None, 'tools':{'items':items,'note':''}}
    (output / 'demo-data.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output / '.nojekyll').write_text('',encoding='utf-8')
    print('Built public catalog with no research projects:', output)


if __name__ == '__main__':
    build()
