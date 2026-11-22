"""Record a real user's approval AFTER presenting a frozen plan. Not an MCP tool."""
import argparse
import json
from harness.experiments import approve_proposal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("proposal_id")
parser.add_argument("--user-approval-text", required=True)
parser.add_argument("--max-runs", type=int, default=1)
parser.add_argument("--allow-code-iterations", action="store_true")
parser.add_argument("--mutable-parameter", action="append", default=[])
args = parser.parse_args()
print(json.dumps(approve_proposal(args.proposal_id, args.user_approval_text, args.max_runs, args.allow_code_iterations, args.mutable_parameter), ensure_ascii=True, indent=2))
