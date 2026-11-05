#!/bin/bash
# Fetch new statement emails from Gmail, then rebuild out/expense_breakdown.xlsx, out/dashboard.html and
# out/summary.json for every month. Exits 1 if any statement fails its checks.
#
#   ./refresh.sh             emails from the last 120 days (ones already downloaded are skipped)
#   ./refresh.sh --no-fetch  rebuild from the PDFs already in statements/
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
export PYTHONWARNINGS=ignore  # Google's libraries warn about Python 3.9 on every run

for f in rules gmail_sources; do
  [[ -f config/$f.yaml ]] || { echo "Missing config/$f.yaml: copy config/$f.example.yaml and edit it"; exit 1; }
done

if [[ "${1:-}" != "--no-fetch" ]]; then
  echo "== Fetching statements from Gmail"
  $PY fetch_gmail.py --since "$(date -v-120d +%Y/%m/%d)"
fi

echo "== Building reports"
[[ -f out/summary.json ]] && cp out/summary.json out/summary.prev.json  # /refresh reports what changed
$PY run.py

$PY - <<'EOF'
import json, sys

def inr(x):  # 238064 -> 2,38,064
    s = f"{round(x):d}"
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        head, tail = head[:-2], head[-2:] + "," + tail
    return f"₹{head + ',' if head else ''}{tail}"

s = json.load(open("out/summary.json"))
print("\n== Months")
for m in s["months"]:
    print(f"  {m['label']}  {inr(m['total']):>10}  {m['coverage']:6.1%} covered{'' if m['complete'] else '  (partial)'}")
if s["unmatched"]:
    print(f"\n{len(s['unmatched'])} payee(s) no rule matched, now under Uncategorized:")
    for u in s["unmatched"]:
        print(f"  {u['merchant']}: {inr(u['total'])} {u['direction']}, {u['count']}x")
print("\nDashboard: out/dashboard.html")
sys.exit(1 if s["failed_checks"] else 0)
EOF
