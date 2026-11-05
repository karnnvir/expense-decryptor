"""Write out/summary.json: what /refresh reads to report, ask about unknown payees and update Notion.

Every month, not only --month. Amounts are signed the way the workbook signs them:
spend is net of refunds, investments are net of money coming back, income is net of reversals."""
import json
from collections import defaultdict
from datetime import date


def _signed(t):
    return t.amount if t.direction == "debit" else -t.amount


def build(statements, transactions, skipped, failed, dash):
    months = []
    for i, m in enumerate(dash["months"]):
        cats = {c: v[i] for c, v in dash["categories"].items() if round(v[i], 2)}
        months.append({**m, "categories": dict(sorted(cats.items(), key=lambda kv: -kv[1]))})

    not_spend = defaultdict(lambda: {"transfers_out": 0.0, "invested_net": 0.0, "income": 0.0})
    for t in transactions:
        row = not_spend[t.date.strftime("%b %Y")]
        if t.kind == "transfer" and t.direction == "debit":
            row["transfers_out"] += t.amount
        elif t.kind == "investment":
            row["invested_net"] += _signed(t)
        elif t.kind == "income":
            row["income"] -= _signed(t)
    for m in months:
        m["not_spend"] = {k: round(v, 2) for k, v in not_spend[m["label"]].items()}

    # Rows no rule matched, one entry per payee: these are the questions for the owner.
    unknown = defaultdict(list)
    for t in transactions:
        if t.review and t.category == "Uncategorized":
            unknown[(t.merchant, t.direction)].append(t)
    unmatched = [{
        "merchant": merchant,
        "direction": direction,
        "count": len(ts),
        "total": round(sum(t.amount for t in ts), 2),
        "accounts": sorted({t.account for t in ts}),
        "dates": sorted(t.date.isoformat() for t in ts),
        "example": ts[0].raw_desc[:160],
    } for (merchant, direction), ts in sorted(unknown.items(), key=lambda kv: -sum(t.amount for t in kv[1]))]

    return {
        "built": date.today().isoformat(),
        "statement_pdfs": dash["statements"],
        "statements": len(statements),
        "skipped": [{"what": w, "reason": r} for w, r in skipped],
        "failed_checks": [{"check": n, "statement": e, "parsed": a} for n, e, a, _ in failed],
        "covered_to": {a: max(st.period_end for st in statements if st.account == a).isoformat()
                       for a in sorted({st.account for st in statements})},
        "months": months,
        "unmatched": unmatched,
        "to_verify": sum(t.review and t.category != "Uncategorized" for t in transactions),
    }


def write(path, *args):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build(*args), f, ensure_ascii=False, indent=1)
