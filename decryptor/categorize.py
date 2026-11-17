"""Reconcile and categorize transactions using config/rules.yaml."""
import re
from datetime import timedelta

import yaml

SPEND_KINDS = {"spend", "refund"}


def load_rules(path: str):
    cfg = yaml.safe_load(open(path))
    rules = []
    for r in cfg["rules"]:
        rules.append({**r, "regex": re.compile(r["match"], re.IGNORECASE)})
    return cfg["categories"], rules


def guess_merchant(t) -> str:
    """Best-effort clean name for a transaction no rule knows yet."""
    if " UPI/" in t.raw_desc:  # ICICI: "Name UPI/..."
        return t.raw_desc.split(" UPI/")[0].strip().title()
    if re.match(r"UPI/(DR|CR)/\d+/", t.raw_desc):  # SBI: "UPI/DR/<ref>/<NAME>/<bank>/..."
        return t.raw_desc.split("/")[3].strip().title()
    if t.raw_desc.startswith("UPI/"):  # ICICI emailed statement: "UPI/<NAME, cut to 10>/<vpa>/..."
        return t.raw_desc.split("/")[1].strip().title()
    if t.raw_desc.startswith("UPI-"):  # HDFC UPI RuPay card: "UPI-<NAME>"
        return t.raw_desc[4:].replace("_", " ").strip().title()
    return re.sub(r"\s+IN$|\s*\(Ref# \d+\)", "", t.raw_desc).strip()  # HDFC cards end rows with "(Ref# <n>)"


def categorize(transactions, rules):
    """Transactions must be in statement order: "same as previous" rules look at the row above."""
    prev = None
    for t in transactions:
        for r in rules:
            if r.get("direction") and r["direction"] != t.direction:
                continue
            if r.get("account") and r["account"] not in t.account:
                continue
            if r.get("max_amount") and t.amount >= r["max_amount"]:
                continue
            m = r["regex"].search(t.raw_desc)
            if not m:
                continue
            if "merchant" in r:
                t.merchant = r["merchant"].format(*[g.strip() for g in m.groups() if g])
            else:
                t.merchant = guess_merchant(t)
            t.category = r["category"]
            if t.category == "same as previous":
                if prev is not None and prev.account == t.account:
                    t.category = prev.category
                    t.merchant = f"{t.merchant} on {prev.merchant}"
                else:
                    t.category = "Uncategorized"
            t.kind = r.get("kind", "spend")
            t.review = bool(r.get("review"))
            t.note = "; ".join(x for x in (t.note, r.get("note", "")) if x)
            break
        else:
            t.merchant = guess_merchant(t)
            t.category = "Uncategorized"
            t.kind = "spend" if t.direction == "debit" else "income"
            t.review = True
        if t.kind == "spend" and t.direction == "credit":
            t.kind = "refund"
        if not t.merchant.startswith("GST"):
            prev = t


def reconcile(transactions):
    """Cancel reversals: a card credit that exactly matches an earlier debit to the same merchant
    (EMI conversions, failed orders). Both sides drop out of spend."""
    used = set()
    for c in transactions:
        if c.direction != "credit" or " CC " not in c.account:
            continue
        if c.amount == 0:
            c.kind, c.note = "reversed", "Zero-value adjustment"
            continue
        for d in transactions:
            if (
                d.direction == "debit"
                and id(d) not in used
                and d.account == c.account
                and d.raw_desc == c.raw_desc
                and abs(d.amount - c.amount) < 0.01
                and timedelta(0) <= c.date - d.date <= timedelta(days=7)
            ):
                used.add(id(d))
                for t in (c, d):
                    t.kind = "reversed"
                    t.review = False
                    t.note = "; ".join(x for x in (t.note, "Cancelled out by a matching reversal") if x)
                break


def match_card_payments(transactions):
    """A bank debit that exactly matches a payment received on one of the cards within 3 days is that
    card's bill being paid, whichever app it went through (CRED, Tata Pay, BBPS...). Without this it
    would be counted twice: once as the bank debit, once as the card purchases it paid for.
    A CRED payment with no matching card stays flagged: it pays a card whose statement we don't have."""
    received = [t for t in transactions
                if " CC " in t.account and t.kind == "transfer" and t.direction == "credit"]
    # Bill-pay rows already named after their card (e.g. "CC BillPay-1234") need no matching.
    candidates = [t for t in transactions
                  if " SB " in t.account and t.direction == "debit"
                  and (t.kind == "spend" or t.merchant == "Card bill payment via CRED")]
    for t in candidates:
        for r in received:
            if abs(r.amount - t.amount) < 0.01 and abs((r.date - t.date).days) <= 3:
                # Name the app from the statement text: a rule may already have renamed the payee (e.g. Tata Pay -> BigBasket)
                via = "CRED" if "CRED" in t.merchant else guess_merchant(t)
                t.merchant = f"Card bill payment XX{r.account[-4:]} via {via}"
                t.category, t.kind, t.review = "Transfers", "transfer", False
                t.note = f"Matches the payment received on {r.account} on {r.date:%d %b}"
                received.remove(r)
                break


def run(transactions, rules_path: str):
    categories, rules = load_rules(rules_path)
    categorize(transactions, rules)
    reconcile(transactions)
    match_card_payments(transactions)
    return categories
