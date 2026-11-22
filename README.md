# ai2research

A research workbench with persistent tasks, bounded experiments, evidence tracking, independent reviews and a lightweight progress dashboard.

面向研究助手的工作台：从选题、论文与代码核查，到实验、复核、图表、稿件和交付，记录每一步的真实依据。

## What is included

- CLI and stdio MCP tools backed by the same SQLite state.
- Durable tasks, dependencies, handoffs, experiment proposals and approval scopes.
- Local/SSH experiment workers, result collection, independent evaluation and bounded recovery.
- Frozen review packets, review receipts, chapter-level writing and reproducible delivery bundles.
- A buildless HTML/CSS/JS dashboard with twelve research milestones, references and a tool inventory.
- Research workflow skills and regression tests.

Research reasoning and code changes are coordinated by an active host agent, such as Codex. This release is a single-user workbench; it does not include an unattended research agent service or multi-tenant hosting.

## Quick start

The reference environment uses Python 3.14.4 for the harness and Python 3.12.2 for the isolated arXiv adapter, managed by `uv`.

```bash
git clone https://github.com/ntdaidai/ai2research.git
cd ai2research
bash tools/bootstrap_linux.sh
./ai2research showcase
```

Open `http://127.0.0.1:8765/`. An empty installation shows a start guide; it contains no private research records.

Before local or SSH experiments, edit `[remote]` in `harness.toml`: set your account, absolute home and `<home>/.research-harness` workspace. Set GPU indices to the devices you may use. New external-review calls are disabled by default; configure your provider, model, key environment name and a bounded call allowance explicitly. Never put the key in the repository.

Optional user skills and MCP registration:

```bash
.venv/bin/python tools/install_local_skill.py --dry-run
.venv/bin/python tools/install_local_skill.py
.venv/bin/python tools/configure_harness_mcp.py
```

The installer preserves unrelated existing skills and reports conflicts. The bootstrap does not change existing agent configuration unless `AI2RESEARCH_INSTALL_SKILLS=1` is set.

## Start or inspect a project

```bash
./ai2research list_projects
./ai2research start 'https://arxiv.org/abs/YOUR_PAPER_ID' --project my-study
./ai2research project_overview '{"project_id":"my-study"}'
```

You can also start from a domain with `start --domain ... --requirements ... --as-of YYYY-MM-DD`. Ask your host agent to inspect existing work before resuming. A discussion or record is not an experiment approval. See [framework guide](research/FRAMEWORK_GUIDE.md) for the workflow and [dashboard guide](web/showcase/README.md) for presentation details.

## Deploy the live dashboard

For a trusted LAN, bind your server address:

```bash
./ai2research showcase --host YOUR_SERVER_IP --port 8765
```

For a restartable user service on a systemd Linux host:

```bash
.venv/bin/python tools/deploy_showcase.py --host YOUR_SERVER_IP --port 8765
systemctl --user status ai2research-showcase
journalctl --user -u ai2research-showcase
```

The dashboard reads local state. It does not start jobs or call paid models. Anyone able to reach the bound port can view that state. Use SSH forwarding for private access. The user service survives SSH disconnects when the machine enables user lingering; it does not require a system service or root installation.

## Public static demo

`docs/` contains a generated interface demo, clearly labeled as synthetic. It does not connect to a research server or expose private state. Generate it with:

```bash
python3 tools/build_public_demo.py
python3 -m http.server 8080 --directory docs
```

The demo can be deployed on any static HTTP server. To serve an exported snapshot of your own research without a running research backend:

```bash
.venv/bin/python tools/export_showcase_static.py --output .local/showcase-static
.venv/bin/python tools/deploy_showcase.py --host YOUR_SERVER_IP --port 8765 --static-directory .local/showcase-static
```

This service only serves files. The snapshot includes selected project metadata and verified presentation outputs; use a trusted LAN or add your own access gateway. Re-export when research records change. No model or experiment process runs as part of serving the page.

## Validation

```bash
.venv/bin/python -B -m unittest discover -s tests -q
```

These are infrastructure and workflow checks using isolated fixtures. They do not establish research novelty, benchmark superiority or venue acceptance. Optional Firefox browser QA is described in the dashboard guide.

## License

[MIT](LICENSE). Dependency and reference notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The dashboard has four separate views: overview, research progress, results, and tools/references. Navigation supports direct links and browser back. Progress counts verified final handover as preparation evidence while preserving legacy task handoff gaps in the stage notes. A changed delivery or a newer review reopens verification.
