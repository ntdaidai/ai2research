"""Build a static, explicitly synthetic UI demo without reading local research."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
STAGES = [
    ("intake", "选题入口", "spark"), ("papers", "文章筛选", "book"),
    ("code", "代码核查", "code"), ("selection", "可行课题", "node"),
    ("preparation", "实验准备", "code"), ("experiments", "实验运行", "flask"),
    ("verification", "复核消融", "check"), ("experiment_review", "实验审查", "check"),
    ("manuscript", "论文初稿", "file"), ("figures", "图表制作", "layers"),
    ("manuscript_review", "稿件审查", "check"), ("delivery", "成果交付", "layers")]


def build():
    output = ROOT / "docs"
    output.mkdir(exist_ok=True)
    for name in ("index.html", "style.css", "app.js", "favicon.svg"):
        shutil.copyfile(ROOT / "web/showcase" / name, output / name)
    html = (output / "index.html").read_text()
    html = html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-demo="true">')
    html = html.replace('href="/style.css"', 'href="./style.css"').replace('src="/app.js"', 'src="./app.js"').replace('href="/favicon.svg"', 'href="./favicon.svg"').replace('href="/"', 'href="./"')
    html = html.replace('真实研究记录 · 自动同步 · 随时回看', '公开界面演示 · 全部为合成示例 · 不连接研究服务器')
    html = html.replace('研究工作台</span>', '界面演示</span>')
    (output / "index.html").write_text(html)
    script = (output / "app.js").read_text()
    script = script.replace('fetch(`/api/snapshot${query}`,', 'fetch(document.documentElement.dataset.demo === "true" ? "./demo-data.json" : `/api/snapshot${query}`,')
    script = script.replace('`本地记录 · ${date(data.generated_at)} · 每30秒同步`', '`公开合成示例 · 非真实科研结果`')
    script = script.replace('const project = current?.project;\n  if (!project)', 'const project = document.documentElement.dataset.demo === "true" ? null : current?.project;\n  if (!project)')
    (output / "app.js").write_text(script)
    stages=[]
    for index,(identifier,title,icon) in enumerate(STAGES):
        complete=index<6
        status='completed' if complete else 'in_progress' if index==6 else 'pending'
        stages.append({'id':identifier,'title':title,'icon':icon,'state':status,
          'state_label':{'completed':'已完成','in_progress':'进行中','pending':'未开始'}[status],
          'completed_checks':1 if complete else 0,'total_checks':1,
          'checks':[{'label':'合成示例：本阶段材料与记录','done':complete,'detail':''}],
          'summary':'界面演示数据，不代表实际阅读、实验、审查或发表。',
          'next_action':'运行真实工作台后，在已有授权范围内推进并更新记录。',
          'evidence':[],'notes':['所有状态与数量均为合成界面示例。'],'items':[]})
    project={'id':'demo-study','title':'示例研究 · 合成数据','execution_state':'active','evidence_kind':'toolchain_test',
       'job_counts':{'completed':8,'failed':1,'timeout':1},'jobs_total':10,'running_jobs':0,
       'jobs':[{'id':f'demo-job-{i+1:02}','state':'completed' if i<8 else 'failed' if i==8 else 'timeout','collected':True} for i in range(10)],
       'artifact_count':24,'artifact_kinds':{},'outputs':[],'reviews':[],'activity':[],
       'task_counts':{'completed':6,'pending':6},'last_event_at':None,
       'progress':{'stages':stages,'completed_stages':6,'total_stages':12,'attention_stages':0,
          'focus_stage':'verification','paused':False,'next_action':'此为合成界面演示，请在自己的工作台开始真实研究。'},
       'references':{'state':'empty','items':[],'note':'公开演示不包含私人选题、论文清单或实验代码；真实项目在这里显示已登记的PDF/GitHub链接。'}}
    snapshot={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'mode':'synthetic_demo',
       'projects':[{'id':project['id'],'title':project['title'],'execution_state':'active','evidence_kind':'toolchain_test'}],
       'project':project,'tools':{'items':[], 'note':'工具清单来自真实部署时的本机检测；此公开静态演示不探测访问者环境。'}}
    (output / 'demo-data.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n')
    (output / '.nojekyll').write_text('')
    print('Built synthetic static demo:',output)


if __name__=='__main__':
    build()
