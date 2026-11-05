"""Expense Decryptor v0: statements/ in, out/expense_breakdown.xlsx out.

    .venv/bin/python run.py

Each bank's PDF password is read from config/passwords.yaml (git-ignored), then STATEMENT_PASSWORD_<BANK>
(e.g. STATEMENT_PASSWORD_SBI), or asked for at the prompt.
"""
import argparse
import getpass
import os
from pathlib import Path

import yaml

from decryptor import categorize, dashboard, parsers, report, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--statements", default="statements")
    ap.add_argument("--rules", default="config/rules.yaml")
    ap.add_argument("--month", help="only report this month, e.g. 2026-09")
    ap.add_argument("--out")
    ap.add_argument("--dashboard", default="out/dashboard.html", help="charts of every month, open in a browser")
    ap.add_argument("--summary", default="out/summary.json", help="every month as JSON, read by /refresh")
    args = ap.parse_args()
    args.out = args.out or f"out/expense_breakdown{'_' + args.month if args.month else ''}.xlsx"

    pw_file = Path("config/passwords.yaml")
    passwords = (yaml.safe_load(pw_file.read_text()) or {}) if pw_file.exists() else {}
    passwords = {k: str(v) for k, v in passwords.items()}

    def password_for(folder):
        # A folder's own key ("hdfc-bank") wins over its bank's ("hdfc"): HDFC cards and accounts differ
        if folder in passwords:
            return passwords[folder]
        bank = folder.split("-")[0]
        if bank not in passwords:
            passwords[bank] = (os.environ.get(f"STATEMENT_PASSWORD_{bank.upper()}")
                               or getpass.getpass(f"{bank.upper()} statement PDF password: "))
        return passwords[bank]

    statements, skipped = parsers.parse_folder(args.statements, password_for)
    for what, reason in skipped:
        print(f"SKIPPED  {what}: {reason}")
    transactions = [t for st in statements for t in st.transactions]

    failed = [c for st in statements for c in st.checks if not c[3]]
    for name, expected, actual, _ in failed:
        print(f"CHECK FAILED  {name}: statement {expected:,.2f}, parsed {actual:,.2f}")

    # Categorize and reconcile everything first: a reversal can land in a different month from its purchase.
    categories = categorize.run(transactions, args.rules)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.dashboard).parent.mkdir(parents=True, exist_ok=True)
    dash = dashboard.write(args.dashboard, statements, transactions, categories, args.rules)  # always every month
    summary.write(args.summary, statements, transactions, skipped, failed, dash)
    if args.month:
        transactions = [t for t in transactions if t.date.strftime("%Y-%m") == args.month]
    report.write(args.out, statements, transactions, categories, skipped)

    review = sum(t.review for t in transactions)
    print(f"{len(statements)} statements, {len(transactions)} transactions, "
          f"{len(failed)} failed checks, {review} to review -> {args.out}, {args.dashboard}")


if __name__ == "__main__":
    main()
