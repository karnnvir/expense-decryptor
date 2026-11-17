"""Write the monthly breakdown workbook. Every total is a formula over the Transactions tab,
so re-categorizing a row there updates the Summary."""
import calendar
import re
from datetime import date

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FONT = "Arial"
MONEY = '#,##0.00;-#,##0.00;"-"'
HEADER_FILL = PatternFill("solid", fgColor="E7E6E6")
REVIEW_FILL = PatternFill("solid", fgColor="FFF2CC")
EXTRA_CATEGORIES = ["Transfers", "Investments", "Income"]
TXN_ROWS = "$2:$5000"  # generous fixed range so rows added by hand are still summed


def month_label(d: date) -> str:
    return d.strftime("%b %Y")


def _font(ws):
    for row in ws.iter_rows():
        for c in row:
            c.font = Font(name=FONT, size=10, bold=c.font.bold, italic=c.font.italic, color=c.font.color)


def _header(ws, row, values, col=1):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=col + i, value=v)
        c.font = Font(bold=True)
        c.fill = HEADER_FILL


def _col(name):
    """Whole-column reference into the Transactions tab."""
    letter = {"month": "B", "merchant": "D", "category": "E", "kind": "F", "counts": "G", "amount": "H", "review": "I"}[name]
    return f"Transactions!${letter}$2:${letter}$5000"


def coverage(statements, months):
    """Per account (merging consecutive statements of the same account), which days of each month are covered."""
    periods = {}
    for st in statements:
        periods.setdefault(st.account, []).append((st.period_start, st.period_end))
    rows = []
    for account, spans in periods.items():
        cells = []
        for (y, m) in months:
            days = calendar.monthrange(y, m)[1]
            covered = [d for d in range(1, days + 1)
                       if any(lo <= date(y, m, d) <= hi for lo, hi in spans)]
            name = date(y, m, 1).strftime("%b")
            missing = sorted(set(range(covered[0], covered[-1] + 1)) - set(covered)) if covered else []
            if not covered:
                cells.append("not in folder")
            elif len(covered) == days:
                cells.append(f"{name} 1–{days} (full)")
            elif missing:  # a gap between two statements, e.g. a bank that skips a day between cycles
                cells.append(f"{name} {covered[0]}–{covered[-1]} only, except {name} {', '.join(map(str, missing))}")
            else:
                cells.append(f"{name} {covered[0]}–{covered[-1]} only")
        rows.append((account, cells))
    return rows


def write(path, statements, transactions, categories, skipped=()):
    transactions = sorted(transactions, key=lambda t: (t.date, t.account))
    months = sorted({(t.date.year, t.date.month) for t in transactions})
    month_names = [month_label(date(y, m, 1)) for y, m in months]

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    txn_ws = wb.create_sheet("Transactions")
    review_ws = wb.create_sheet("Review")
    checks_ws = wb.create_sheet("Checks")
    lists_ws = wb.create_sheet("Lists")

    # ---------------------------------------------------------------- Lists (dropdown source)
    all_categories = categories + EXTRA_CATEGORIES
    for i, c in enumerate(all_categories, start=1):
        lists_ws.cell(row=i, column=1, value=c)
    lists_ws.sheet_state = "hidden"

    # ---------------------------------------------------------------- Transactions
    cols = ["Date", "Month", "Account", "Merchant", "Category", "Kind", "Counts as spend",
            "Amount (₹, + out / − in)", "Needs review", "Note", "Statement description", "ID"]
    _header(txn_ws, 1, cols)
    for r, t in enumerate(transactions, start=2):
        signed = t.amount if t.direction == "debit" else -t.amount
        values = [t.date, month_label(t.date), t.account, t.merchant, t.category, t.kind,
                  f'=IF(OR(F{r}="spend",F{r}="refund"),"Yes","No")', signed,
                  "Yes" if t.review else "No", t.note, t.raw_desc, t.id]
        for c, v in enumerate(values, start=1):
            cell = txn_ws.cell(row=r, column=c, value=v)
            if t.review:
                cell.fill = REVIEW_FILL
        txn_ws.cell(row=r, column=1).number_format = "dd-mmm-yyyy"
        txn_ws.cell(row=r, column=8).number_format = MONEY
    last = len(transactions) + 1
    dv = DataValidation(type="list", formula1=f"=Lists!$A$1:$A${len(all_categories)}", allow_blank=False)
    dv.add(f"E2:E{last + 200}")
    txn_ws.add_data_validation(dv)
    txn_ws.cell(row=1, column=5).comment = Comment(
        "Pick a category from the dropdown to re-categorize a row. The Summary updates automatically.", "Expense Decryptor")
    txn_ws.freeze_panes = "A2"
    txn_ws.auto_filter.ref = f"A1:L{last}"
    for c, w in enumerate([12, 10, 14, 32, 18, 11, 9, 16, 9, 48, 60, 14], start=1):
        txn_ws.column_dimensions[get_column_letter(c)].width = w

    # ---------------------------------------------------------------- Summary
    s = summary
    s["A1"] = "Expense breakdown"
    s["A1"].font = Font(bold=True, size=14)
    s["A2"] = f"Built from {len({st.source_file for st in statements})} statement PDFs on {date.today():%d %b %Y}. " \
              "Totals are formulas over the Transactions tab."
    s["A2"].font = Font(italic=True)

    row = 4
    s.cell(row=row, column=1, value="Coverage").font = Font(bold=True, size=12)
    row += 1
    _header(s, row, ["Account"] + month_names)
    for account, cells in coverage(statements, months):
        row += 1
        s.cell(row=row, column=1, value=account)
        for i, v in enumerate(cells, start=2):
            s.cell(row=row, column=i, value=v)

    row += 2
    s.cell(row=row, column=1, value="Spend by category (₹)").font = Font(bold=True, size=12)
    row += 1
    hdr = row
    _header(s, hdr, ["Category"] + month_names + ["Total"])
    total_col = len(month_names) + 2
    first_cat = hdr + 1
    for cat in categories:
        row += 1
        s.cell(row=row, column=1, value=cat)
        for i in range(len(month_names)):
            col = get_column_letter(i + 2)
            s.cell(row=row, column=i + 2,
                   value=f'=SUMIFS({_col("amount")},{_col("category")},$A{row},{_col("month")},{col}${hdr},{_col("counts")},"Yes")')
        s.cell(row=row, column=total_col, value=f"=SUM(B{row}:{get_column_letter(total_col - 1)}{row})")
    last_cat = row
    row += 1
    s.cell(row=row, column=1, value="Total spend").font = Font(bold=True)
    for c in range(2, total_col + 1):
        col = get_column_letter(c)
        s.cell(row=row, column=c, value=f"=SUM({col}{first_cat}:{col}{last_cat})").font = Font(bold=True)
    spend_total_row = row
    for r in range(first_cat, row + 1):
        for c in range(2, total_col + 1):
            s.cell(row=r, column=c).number_format = MONEY

    row += 2
    s.cell(row=row, column=1, value="Not counted as spend (₹)").font = Font(bold=True, size=12)
    row += 1
    nhdr = row
    _header(s, nhdr, ["Item"] + month_names)
    not_spend = [
        ("Card payments and self-transfers out", f'{_col("kind")},"transfer",{_col("month")},{{m}},{_col("amount")},">0"', 1),
        ("Net invested via ICICI Direct", f'{_col("kind")},"investment",{_col("month")},{{m}}', 1),
        ("Income: interest and dividends", f'{_col("kind")},"income",{_col("month")},{{m}}', -1),
    ]
    for label, crit, sign in not_spend:
        row += 1
        s.cell(row=row, column=1, value=label)
        for i in range(len(month_names)):
            m = f"{get_column_letter(i + 2)}${nhdr}"
            prefix = "-" if sign < 0 else ""
            cell = s.cell(row=row, column=i + 2, value=f'={prefix}SUMIFS({_col("amount")},{crit.format(m=m)})')
            cell.number_format = MONEY

    row += 2
    s.cell(row=row, column=1, value="Waiting for your review").font = Font(bold=True, size=12)
    row += 1
    s.cell(row=row, column=1, value="Transactions")
    s.cell(row=row, column=2, value=f'=COUNTIFS({_col("review")},"Yes")')
    row += 1
    s.cell(row=row, column=1, value="Amount (₹)")
    s.cell(row=row, column=2, value=f'=SUMIFS({_col("amount")},{_col("review")},"Yes")').number_format = MONEY

    row += 2
    s.cell(row=row, column=1, value="Read before trusting the totals").font = Font(bold=True, size=12)
    for note in build_caveats(statements, transactions, months, skipped):
        row += 1
        s.cell(row=row, column=1, value=f"• {note}")
        s.cell(row=row, column=1).alignment = Alignment(wrap_text=False)

    s.column_dimensions["A"].width = 38
    for c in range(2, total_col + 1):
        s.column_dimensions[get_column_letter(c)].width = 22

    # ---------------------------------------------------------------- Review (one row per merchant)
    _header(review_ws, 1, ["Merchant", "Transactions", "Amount (₹)", "Current category", "What I need from you"])
    seen = {}
    for t in transactions:
        if t.review and t.merchant not in seen:
            seen[t.merchant] = t
    for r, (merchant, t) in enumerate(seen.items(), start=2):
        review_ws.cell(row=r, column=1, value=merchant)
        review_ws.cell(row=r, column=2, value=f'=COUNTIFS({_col("merchant")},$A{r},{_col("review")},"Yes")')
        review_ws.cell(row=r, column=3, value=f'=SUMIFS({_col("amount")},{_col("merchant")},$A{r},{_col("review")},"Yes")').number_format = MONEY
        review_ws.cell(row=r, column=4, value=t.category)
        review_ws.cell(row=r, column=5, value=t.note or "What is this payment for?")
    review_ws.freeze_panes = "A2"
    for c, w in enumerate([34, 13, 14, 20, 80], start=1):
        review_ws.column_dimensions[get_column_letter(c)].width = w

    # ---------------------------------------------------------------- Checks
    _header(checks_ws, 1, ["Check", "From the statement (₹)", "Parsed (₹)", "Result"])
    r = 1
    for st in statements:
        for name, expected, actual, ok in st.checks:
            r += 1
            checks_ws.cell(row=r, column=1, value=name)
            checks_ws.cell(row=r, column=2, value=expected).number_format = MONEY
            checks_ws.cell(row=r, column=3, value=actual).number_format = MONEY
            checks_ws.cell(row=r, column=4, value=f'=IF(ABS(B{r}-C{r})<0.01,"Pass","FAIL")')
    checks_ws.column_dimensions["A"].width = 62
    for c in "BCD":
        checks_ws.column_dimensions[c].width = 22

    for ws in (summary, txn_ws, review_ws, checks_ws):
        _font(ws)
    wb.save(path)


def build_caveats(statements, transactions, months, skipped=()):
    notes = [f"Skipped {what}: {reason}." for what, reason in skipped if "duplicate" not in reason]
    cards_paid = sorted({m.group(1) for t in transactions
                         if (m := re.match(r"Card bill payment XX(\d{4})", t.merchant))})
    cards_in_folder = {st.account[-4:] for st in statements if " CC " in st.account}
    missing = [c for c in cards_paid if c not in cards_in_folder]
    if missing:
        notes.append(f"Bill payments go to card(s) {', '.join('XX' + c for c in missing)}, but their statements "
                     "are not in the folder, so their purchases are missing.")
    cred = sum(t.amount for t in transactions if t.merchant == "Card bill payment via CRED")
    if cred:
        notes.append(f"₹{cred:,.2f} was paid to credit cards through CRED. Those cards' purchases are missing too.")
    month_names = [month_label(date(y, m, 1)) for y, m in months]
    for account, cells in coverage(statements, months):
        gaps = [f"{name} has {c}" if "only" in c else f"no {name} statement yet"
                for name, c in zip(month_names, cells) if "only" in c or c == "not in folder"]
        if gaps:
            notes.append(f"{account}: {', '.join(gaps)}.")
    return notes
