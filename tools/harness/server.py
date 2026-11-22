from fastmcp import FastMCP
from . import service

mcp = FastMCP("ai2research", instructions="Start from a field and requirements with start_from_request, or supplied papers with start_project. Verify five recent top-venue publications with real open-source code before candidate selection. The live controller delegates durable tasks to independent host agents. Use frozen review packets for agent/DeepSeek reviews and publication_plan for chapter-by-chapter writing. Existing research_cycle executes approved frozen experiments; use the user's existing applicable research authorization without asking repeatedly. No hidden background research daemon.")

for name in ("doctor", "research_cycle", "get_project_state", "record_decision", "probe_server", "submit_smoke", "submit_experiment",
             "prepare_experiment", "submit_gpu_smoke", "collect_job_results", "compare_jobs", "list_jobs", "retry_job_dispatch", "resume_project", "export_project_report",
             "get_job_status", "read_job_logs", "cancel_job", "review_topic", "search_papers", "fetch_paper",
             "read_paper", "export_bibtex", "find_code", "read_code", "compare_runs", "tuning_smoke", "compile_paper", "export_diagram", "parse_pdf",
             "start_project", "project_status", "research_workflow", "create_task", "claim_task", "update_task", "repair_changed_task_evidence", "register_artifact",
             "attach_job_evidence", "prepare_review", "record_review", "run_external_review", "review_status", "create_revision_tasks",
             "build_experiment_matrix", "publication_plan", "assemble_manuscript", "export_results_figure", "export_delivery",
             "review_budget", "toolchain_status", "extract_paper_text", "export_project_diagram", "list_review_packets",
             "validate_result_artifact", "list_projects", "start_from_request", "record_paper_candidates", "set_project_execution_state",
             "project_overview", "process_log", "maintenance_status"):
    mcp.tool(getattr(service, name))


def main():
    mcp.run(transport="stdio", show_banner=False)
