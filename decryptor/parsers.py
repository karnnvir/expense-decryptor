"""Statement parsers: turn an ICICI PDF into a list of Transactions plus validation checks."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pdfplumber


@dataclass
class Transaction:
    date: date
    account: str
    raw_desc: str
    amount: float  # always positive; direction says which way it moved
    direction: str  # "debit" | "credit"
    source_file: str
    ref: str = ""
    # Filled in by categorize.py
    merchant: str = ""
    category: str = ""
    kind: str = ""  # spend | refund | transfer | investment | income | reversed
    review: bool = False
    note: str = ""

    @property
    def id(self) -> str:
        key = f"{self.account}|{self.date}|{self.amount:.2f}|{self.direction}|{self.raw_desc}|{self.ref}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass
class Statement:
    source_file: str
    account: str
    period_start: date
    period_end: date
    transactions: list = field(default_factory=list)
    checks: list = field(default_factory=list)  # (name, expected, actual, ok)


def money(s: str) -> float:
    return float(s.replace(",", "").replace("`", "").replace("₹", ""))


def _check(name, expected, actual):
    return (name, round(expected, 2), round(actual, 2), abs(expected - actual) < 0.01)


# ---------------------------------------------------------------- ICICI bank

# Net banking downloads mask the account (XXXXXXXX1234); emailed statements print it in full
# ("Number: 00XXXXXX1234") and cover several months with one header per account, not per page.
BANK_HEADER = re.compile(
    r"Statement of Transactions in Savings Account (?:Number: )?[X\d]*(\d{4}) in INR for the period "
    r"(\w+ \d{2}, \d{4}) - (\w+ \d{2}, \d{4})"
)
BANK_TOTAL = re.compile(r"^TOTAL ([\d,]+\.\d{2}) ([\d,]+\.\d{2}) ([\d,]+\.\d{2})$")
BANK_ROW = re.compile(r"^(\d{2}-\d{2}-\d{4})\s*(.*?)\s*([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$")
BANK_BF = re.compile(r"^(\d{2}-\d{2}-\d{4})\s+B/F\s+([\d,]+\.\d{2})$")
DATE_COL_MAX_X = 60  # the DATE column starts near x=30; wrapped particulars start near x=140


def parse_icici_bank(path: str, password: str) -> list:
    """One PDF can hold several savings accounts; returns one Statement per account."""
    statements = {}

    def start(m):
        acct = f"ICICI SB {m.group(1)}"
        if acct not in statements:
            st = Statement(
                source_file=path,
                account=acct,
                period_start=datetime.strptime(m.group(2), "%B %d, %Y").date(),
                period_end=datetime.strptime(m.group(3), "%B %d, %Y").date(),
            )
            st.opening = None
            st.balance = None
            statements[acct] = st
        return statements[acct]

    st = None  # the account whose table is running; it carries over to pages without a header
    with pdfplumber.open(path, password=password) as pdf:
        for page in pdf.pages:
            st = _parse_bank_page(page, st, start)

    for st in statements.values():
        # Every row's balance was already checked against the previous one; this is the overall check.
        flows = sum(t.amount if t.direction == "credit" else -t.amount for t in st.transactions)
        st.checks.append(_check(f"{st.account}: opening + credits - debits = closing", st.balance, st.opening + flows))
        del st.balance
    return list(statements.values())


def _parse_bank_page(page, st, start):
    """Reads one page's transaction rows into st. A header line on the page switches to that account
    (start(match) returns its Statement). Returns the account still running at the end of the page."""
    lines = page.extract_text_lines()
    in_table = False
    rows, extras = [], []  # rows: (top, Transaction); extras: (top, text) wrapped particulars
    for ln in lines:
        text = ln["text"].strip()
        if m := BANK_HEADER.search(text):
            st = start(m)
            continue
        if text.startswith("DATE MODE"):
            in_table = st is not None
            continue
        if not in_table:
            continue
        if text.startswith("Total:"):
            break
        if m := BANK_TOTAL.match(text):  # the emailed layout's per-account totals row
            st.checks.append(_check(f"{st.account}: deposits", money(m.group(1)),
                                    sum(t.amount for t in st.transactions if t.direction == "credit")))
            st.checks.append(_check(f"{st.account}: withdrawals", money(m.group(2)),
                                    sum(t.amount for t in st.transactions if t.direction == "debit")))
            in_table = False
            continue
        if ln["x0"] < DATE_COL_MAX_X:
            bf = BANK_BF.match(text)
            if bf:
                st.opening = st.balance = money(bf.group(2))
                continue
            m = BANK_ROW.match(text)
            if not m:
                continue
            amount, balance = money(m.group(3)), money(m.group(4))
            # The statement prints deposits and withdrawals in separate columns; the running
            # balance tells us which one this is, and doubles as a per-row validation.
            if abs(st.balance + amount - balance) < 0.01:
                direction = "credit"
            elif abs(st.balance - amount - balance) < 0.01:
                direction = "debit"
            else:
                st.checks.append(_check(f"{st.account}: running balance on {m.group(1)}", balance, st.balance))
                direction = "debit"
            st.balance = balance
            t = Transaction(
                date=datetime.strptime(m.group(1), "%d-%m-%Y").date(),
                account=st.account,
                raw_desc=m.group(2),
                amount=amount,
                direction=direction,
                source_file=st.source_file,
            )
            rows.append((ln["top"], t))
            st.transactions.append(t)
        else:
            extras.append((ln["top"], text))
    _attach_wrapped(page, rows, extras)
    return st


def _attach_wrapped(page, rows, extras):
    """Wrapped descriptions sit above and below their row's date line, inside the same ruled band.
    rows: (top, Transaction) for each date line; extras: (top, text) for each wrapped line."""
    flat = [l for l in page.lines if abs(l["top"] - l["bottom"]) < 0.5]
    rules = sorted(l["top"] for l in flat if l["x1"] - l["x0"] > 200)
    if not rules:  # emailed statements draw each row rule as one segment per column
        widths = {}
        for l in flat:
            widths[round(l["top"], 1)] = widths.get(round(l["top"], 1), 0) + l["x1"] - l["x0"]
        rules = sorted(top for top, w in widths.items() if w > 200)

    def band(y):
        above = max((r for r in rules if r <= y), default=None)
        below = min((r for r in rules if r > y), default=None)
        return above, below

    # Rebuild each description top to bottom: lines above the date line come first.
    pieces = {id(t): [(ty, t.raw_desc)] for ty, t in rows}
    for y, text in extras:
        a, b = band(y)
        same_band = [t for ty, t in rows if a is not None and b is not None and a <= ty < b]
        if len(same_band) == 1:
            owner = same_band[0]
        elif rows:
            owner = min(rows, key=lambda r: abs(r[0] - y))[1]
        else:
            continue
        pieces[id(owner)].append((y, text))
    for _, t in rows:
        t.raw_desc = " ".join(text for _, text in sorted(pieces[id(t)], key=lambda p: p[0]) if text).strip()


# ---------------------------------------------------------------- ICICI credit card

CARD_ROW = re.compile(
    # date, serial no., description, reward points, optional foreign amount ("5.9 USD"), INR amount, CR
    r"(\d{2}/\d{2}/\d{4})\s+(\d{8,})\s+(.+?)\s+(-?\d+)\s+(?:([\d,]+(?:\.\d+)?\s+[A-Z]{3})\s+)?([\d,]+\.\d{2})(\s*CR)?\s*$"
)
CARD_NUMBER = re.compile(r"\d{4}X{8}(\d{4})")
CARD_PERIOD = re.compile(r"Statement period\s*:\s*(\w+ \d{1,2}, \d{4}) to (\w+ \d{1,2}, \d{4})")
CARD_TOTALS = re.compile(
    r"Previous Balance Purchases / Charges Cash Advances Payments / Credits\s*\n"
    r"`([\d,]+\.\d{2})\s+`([\d,]+\.\d{2})\s+`([\d,]+\.\d{2})\s+`([\d,]+\.\d{2})"
)
CARD_DUE = re.compile(r"Total Amount due\s*\n`([\d,]+\.\d{2})")


def parse_icici_card(path: str, password: str) -> list:
    with pdfplumber.open(path, password=password) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    full = "\n".join(pages)

    last4 = CARD_NUMBER.search(full).group(1)
    period = CARD_PERIOD.search(full)
    st = Statement(
        source_file=path,
        account=f"ICICI CC {last4}",
        period_start=datetime.strptime(period.group(1), "%B %d, %Y").date(),
        period_end=datetime.strptime(period.group(2), "%B %d, %Y").date(),
    )

    last = None
    for line in full.splitlines():
        m = CARD_ROW.search(line)
        if m:
            last = Transaction(
                date=datetime.strptime(m.group(1), "%d/%m/%Y").date(),
                account=st.account,
                raw_desc=m.group(3).strip(),
                amount=money(m.group(6)),
                direction="credit" if m.group(7) else "debit",
                source_file=path,
                ref=m.group(2),
            )
            if m.group(5):
                last.note = f"International amount {m.group(5)}"
            st.transactions.append(last)
        elif last and line.startswith("<"):
            # EMI rows continue on the next line, e.g. "<1/9>RSPSOME MERCHANT"
            last.raw_desc = f"{last.raw_desc} {line.strip()}"
        elif line.startswith("EMI / PERSONAL LOAN"):
            last = None

    prev, purchases, cash, credits = (money(x) for x in CARD_TOTALS.search(full).groups())
    due = money(CARD_DUE.search(full).group(1))
    debits = sum(t.amount for t in st.transactions if t.direction == "debit")
    creds = sum(t.amount for t in st.transactions if t.direction == "credit")
    st.checks += [
        _check(f"{st.account}: purchases & charges", purchases + cash, debits),
        _check(f"{st.account}: payments & credits", credits, creds),
        _check(f"{st.account}: previous + charges - credits = total due", due, prev + debits - creds),
    ]
    return [st]


# ---------------------------------------------------------------- SBI bank

SBI_ACCOUNT = re.compile(r"SAVING ACCOUNT\s*\nX+(\d{4})")
# The opening label extracts as garbled text ("Yourn Oupllening nBualllance on 01-09-26: …"), so match
# on the date pattern: the first such line is the opening balance, the "Closing" one the closing balance.
SBI_BALANCE_LINE = re.compile(r"on (\d{2}-\d{2}-\d{2}):\s*([\d,]+\.\d{2})")
SBI_CLOSING = re.compile(r"Your Closing Balance on (\d{2}-\d{2}-\d{2}):\s*([\d,]+\.\d{2})")
# date, description (may be empty when it wraps), ref/cheque no. ("-" when none), credit, debit, balance
SBI_ROW = re.compile(r"^(\d{2}-\d{2}-\d{2})\s*(.*?)\s*(\S+)\s+([\d,]+(?:\.\d{2})?)\s+([\d,]+(?:\.\d{2})?)\s+([\d,]+\.\d{2})$")


def parse_sbi_bank(path: str, password: str) -> list:
    with pdfplumber.open(path, password=password) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)
        opening = SBI_BALANCE_LINE.search(full)
        closing = SBI_CLOSING.search(full)
        st = Statement(
            source_file=path,
            account=f"SBI SB {SBI_ACCOUNT.search(full).group(1)}",
            period_start=datetime.strptime(opening.group(1), "%d-%m-%y").date(),
            period_end=datetime.strptime(closing.group(1), "%d-%m-%y").date(),
        )
        balance = money(opening.group(2))
        in_table = False
        for page in pdf.pages:
            rows, extras = [], []
            for ln in page.extract_text_lines():
                text = ln["text"].strip()
                if text.startswith("Date Transaction Reference"):
                    in_table = True
                    continue
                if text.startswith("Your Closing Balance"):
                    in_table = False
                if not in_table or SBI_BALANCE_LINE.search(text):
                    continue
                if ln["x0"] < DATE_COL_MAX_X - 20 and (m := SBI_ROW.match(text)):
                    credit, debit, new_balance = money(m.group(4)), money(m.group(5)), money(m.group(6))
                    t = Transaction(
                        date=datetime.strptime(m.group(1), "%d-%m-%y").date(),
                        account=st.account,
                        raw_desc=m.group(2),
                        amount=credit or debit,
                        direction="credit" if credit else "debit",
                        source_file=path,
                        ref="" if m.group(3) == "-" else m.group(3),
                    )
                    expected = balance + credit - debit
                    if abs(expected - new_balance) >= 0.01:
                        st.checks.append(_check(f"{st.account}: running balance on {m.group(1)}", new_balance, expected))
                    balance = new_balance
                    rows.append((ln["top"], t))
                    st.transactions.append(t)
                elif in_table:
                    extras.append((ln["top"], text))
            _attach_wrapped(page, rows, extras)

    flows = sum(t.amount if t.direction == "credit" else -t.amount for t in st.transactions)
    st.checks.append(_check(f"{st.account}: opening + credits - debits = closing",
                            money(closing.group(2)), money(opening.group(2)) + flows))
    return [st]


# ---------------------------------------------------------------- Yes Bank credit card

YES_CARD = re.compile(r"Card Number \d{4}X+(\d{4})")
YES_PERIOD = re.compile(r"(\d{2}/\d{2}/\d{4}) To (\d{2}/\d{2}/\d{4})")
YES_PREVIOUS = re.compile(r"Previous Balance :\s*\nRs\. ([\d,]+\.\d{2})\s*(Cr|Dr)?")
# The last "Rs." amount on the line after the label; a non-zero amount may carry a Cr/Dr suffix.
YES_PURCHASES = re.compile(r"& Other Charges :\s*\n.*Rs\. ([\d,]+\.\d{2})(?:\s*(?:Cr|Dr))?\s*$", re.MULTILINE)
YES_PAYMENTS = re.compile(r"Payment & Credits Received :\s*\n.*Rs\. ([\d,]+\.\d{2})(?:\s*(?:Cr|Dr))?\s*$", re.MULTILINE)
YES_DUE = re.compile(r"Total Amount Due:.*\n\s*Rs\. ([\d,]+\.\d{2})\s*(Cr|Dr)?")
# Not yet seen on a real statement (the first one had no transactions); the totals checks will flag a mismatch.
YES_ROW = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([\d,]+\.\d{2})\s*(Dr|Cr)$")


def parse_yes_card(path: str, password: str) -> list:
    with pdfplumber.open(path, password=password) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)

    period = YES_PERIOD.search(full)
    st = Statement(
        source_file=path,
        account=f"YES CC {YES_CARD.search(full).group(1)}",
        period_start=datetime.strptime(period.group(1), "%d/%m/%Y").date(),
        period_end=datetime.strptime(period.group(2), "%d/%m/%Y").date(),
    )
    table = full.split("Statement Details", 1)[-1]
    for line in table.splitlines():
        if m := YES_ROW.match(line.strip()):
            st.transactions.append(Transaction(
                date=datetime.strptime(m.group(1), "%d/%m/%Y").date(),
                account=st.account,
                raw_desc=m.group(2),
                amount=money(m.group(3)),
                direction="credit" if m.group(4) == "Cr" else "debit",
                source_file=path,
            ))

    def signed(m):  # card balances: Cr = in your favour
        return -money(m.group(1)) if m.group(2) == "Cr" else money(m.group(1))

    prev, due = signed(YES_PREVIOUS.search(full)), signed(YES_DUE.search(full))
    purchases, payments = money(YES_PURCHASES.search(full).group(1)), money(YES_PAYMENTS.search(full).group(1))
    debits = sum(t.amount for t in st.transactions if t.direction == "debit")
    creds = sum(t.amount for t in st.transactions if t.direction == "credit")
    st.checks += [
        _check(f"{st.account}: purchases & charges", purchases, debits),
        _check(f"{st.account}: payments & credits", payments, creds),
        _check(f"{st.account}: previous + charges - credits = total due", due, prev + debits - creds),
    ]
    return [st]


# ---------------------------------------------------------------- Kotak credit card

KOTAK_CARD = re.compile(r"Credit Card X(\d{4})")
KOTAK_PERIOD = re.compile(r"Transactions Details from (\d{2}-\w{3}-\d{4}) to (\d{2}-\w{3}-\d{4})")
KOTAK_SUMMARY = {
    "previous": re.compile(r"Previous statement dues (-?[\d,]+\.\d{2})"),  # negative when the card was in credit
    "purchases": re.compile(r"Purchases made in this cycle ([\d,]+\.\d{2})"),
    "fees": re.compile(r"Other fees & charges ([\d,]+\.\d{2})"),
    "credits": re.compile(r"Payments and Other Credits ([\d,]+\.\d{2})"),
    "due": re.compile(r"Total Amount Due \(TAD\) ₹ ([\d,]+\.\d{2})"),
}
# date, description (ends with the bank's spend category, if any), amount, Cr
KOTAK_ROW = re.compile(r"^(\d{2}-\w{3}-\d{4})\s+(.+?)\s+([\d,]+\.\d{2})(\s+Cr)?$")
KOTAK_PAYMENT_TAG = "[Payment]"  # rows under "Payments and Other Credits"; rules.yaml treats them as transfers


def parse_kotak_card(path: str, password: str) -> list:
    with pdfplumber.open(path, password=password) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)

    period = KOTAK_PERIOD.search(full)
    st = Statement(
        source_file=path,
        account=f"KOTAK CC {KOTAK_CARD.search(full).group(1)}",
        period_start=datetime.strptime(period.group(1), "%d-%b-%Y").date(),
        period_end=datetime.strptime(period.group(2), "%d-%b-%Y").date(),
    )
    # Credits in the purchases section (e.g. a fuel surcharge waiver) are netted into "Purchases made",
    # not counted under "Payments and Other Credits", so track which section each row sits in.
    section, purchases_net, payments = None, 0.0, 0.0
    for line in full[period.end():].splitlines():
        line = line.strip()
        if line.startswith("Payments and Other Credits"):
            section = "payments"
            continue
        if line.startswith("Purchases made in this cycle"):
            section = "purchases"
            continue
        if line.startswith("Total Purchases"):
            break
        m = KOTAK_ROW.match(line)
        if not m or section is None:
            continue
        desc = m.group(2).replace("(Convert To EMI)", "").strip()
        credit = bool(m.group(4))
        amount = money(m.group(3))
        if section == "payments":
            desc = f"{KOTAK_PAYMENT_TAG} {desc}"
            payments += amount if credit else -amount
        else:
            purchases_net += -amount if credit else amount
        st.transactions.append(Transaction(
            date=datetime.strptime(m.group(1), "%d-%b-%Y").date(),
            account=st.account,
            raw_desc=re.sub(r"\s+", " ", desc),
            amount=amount,
            direction="credit" if credit else "debit",
            source_file=path,
        ))

    s = {k: money(r.search(full).group(1)) for k, r in KOTAK_SUMMARY.items()}
    debits = sum(t.amount for t in st.transactions if t.direction == "debit")
    creds = sum(t.amount for t in st.transactions if t.direction == "credit")
    st.checks += [
        _check(f"{st.account}: purchases made (net of credits in that section)", s["purchases"] + s["fees"], purchases_net),
        _check(f"{st.account}: payments & other credits", s["credits"], payments),
        _check(f"{st.account}: previous + charges - credits = total due", s["due"], s["previous"] + debits - creds),
    ]
    return [st]


# ---------------------------------------------------------------- HDFC credit card

HDFC_CARD = re.compile(r"Credit Card No\.\s*:?\s*\d{6}X+(\d{4})")
HDFC_PERIOD = re.compile(r"Billing Period (\d{2} \w{3}, \d{4}) - (\d{2} \w{3}, \d{4})")
# previous dues, payments/credits, purchases/debits, finance charges — "C" is how the ₹ sign extracts
HDFC_SUMMARY = re.compile(r"C([\d,]+\.\d{2}) C([\d,]+\.\d{2}) \+ C([\d,]+\.\d{2}) \+ C([\d,]+\.\d{2}) =")
HDFC_DUE = re.compile(r"_ C([\d,]+\.\d{2})")
# date| time, description (empty when it wraps onto the line above), "+" for credits, amount
HDFC_ROW = re.compile(r"^(\d{2}/\d{2}/\d{4})\|\s*\d{2}:\d{2}\s*(.*?)\s*(\+\s*)?C\s*([\d,]+\.\d{2})\s*l?\s*$")


# "Year End Statement & Summary": one financial year, sometimes several cards in one PDF.
HDFC_YE_PERIOD = re.compile(r"Account Summary for the period (\d+)\w\w (\w+ \d{4}) to (\d+)\w\w (\w+ \d{4})")
# 000512345XXXXXX1234 0 C 1,00,000.00 C 90,000.00 C 5,00,000.00
HDFC_YE_CARD = re.compile(r"^0*\d{6}X+(\d{4}) \d+ C ([\d,]+\.\d{2}) C ([\d,]+\.\d{2}) C [\d,]+\.\d{2}$", re.M)
# 06-Apr-2025 UPI-Some Merchant C 300.00 DR 000512345XXXXXX1234
HDFC_YE_ROW = re.compile(r"^(\d{2}-\w{3}-\d{4}) (.*) C ([\d,]+\.\d{2}) (DR|CR) 0*\d{6}X+(\d{4})$", re.M)


def _parse_hdfc_year_end(path: str, full: str) -> list:
    d1, my1, d2, my2 = HDFC_YE_PERIOD.search(full).groups()
    start = datetime.strptime(f"{d1} {my1}", "%d %B %Y").date()
    end = datetime.strptime(f"{d2} {my2}", "%d %B %Y").date()
    cards = {card: Statement(source_file=path, account=f"HDFC CC {card}", period_start=start, period_end=end)
             for card, _, _ in HDFC_YE_CARD.findall(full)}
    for date_s, desc, amount, side, card in HDFC_YE_ROW.findall(full):
        st = cards[card]
        st.transactions.append(Transaction(
            date=datetime.strptime(date_s, "%d-%b-%Y").date(),
            account=st.account,
            raw_desc=desc.strip(),
            amount=money(amount),
            direction="debit" if side == "DR" else "credit",
            source_file=path,
        ))
    for card, debits, credits_ in HDFC_YE_CARD.findall(full):
        st = cards[card]
        st.checks += [
            _check(f"{st.account}: year-end purchases & debits", money(debits),
                   sum(t.amount for t in st.transactions if t.direction == "debit")),
            _check(f"{st.account}: year-end payments & credits", money(credits_),
                   sum(t.amount for t in st.transactions if t.direction == "credit")),
        ]
    return list(cards.values())


def parse_hdfc_card(path: str, password: str) -> list:
    with pdfplumber.open(path, password=password) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)

    if full.startswith("Year End Statement"):
        return _parse_hdfc_year_end(path, full)

    period = HDFC_PERIOD.search(full)
    st = Statement(
        source_file=path,
        account=f"HDFC CC {HDFC_CARD.search(full).group(1)}",
        period_start=datetime.strptime(period.group(1), "%d %b, %Y").date(),
        period_end=datetime.strptime(period.group(2), "%d %b, %Y").date(),
    )
    lines = full.splitlines()
    for i, line in enumerate(lines):
        m = HDFC_ROW.match(line.strip())
        if not m:
            continue
        desc = m.group(2)
        if not desc and i > 0:
            desc = lines[i - 1].strip()
            if i + 1 < len(lines) and lines[i + 1].strip().endswith(")") and not HDFC_ROW.match(lines[i + 1]):
                desc += lines[i + 1].strip()
        st.transactions.append(Transaction(
            date=datetime.strptime(m.group(1), "%d/%m/%Y").date(),
            account=st.account,
            raw_desc=desc,
            amount=money(m.group(4)),
            direction="credit" if m.group(3) else "debit",
            source_file=path,
        ))

    prev, payments, purchases, finance = (money(x) for x in HDFC_SUMMARY.search(full).groups())
    due = money(HDFC_DUE.search(full).group(1))
    debits = sum(t.amount for t in st.transactions if t.direction == "debit")
    creds = sum(t.amount for t in st.transactions if t.direction == "credit")
    computed_due = prev + debits - creds
    st.checks += [
        _check(f"{st.account}: purchases & charges", purchases + finance, debits),
        _check(f"{st.account}: payments & credits", payments, creds),
        # HDFC rounds the total amount due to the nearest rupee
        _check(f"{st.account}: previous + charges - credits = total due (rounded)", due,
               due if abs(due - computed_due) <= 0.5 else computed_due),
    ]
    return [st]


# ---------------------------------------------------------------- Axis credit card

AXIS_CARD = re.compile(r"\n\d{6}\*+(\d{4}) ")
AXIS_PERIOD = re.compile(r"(\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})")
_AMT = r"([\d,]+\.\d{2})"
# Previous balance (Cr/Dr), payments, credits, purchases, cash advance, other debits & charges, total due (Cr/Dr)
AXIS_SUMMARY = re.compile(
    rf"\n{_AMT}\s*(Cr|Dr)? {_AMT} {_AMT} {_AMT} {_AMT} {_AMT} {_AMT}\s*(Cr|Dr)?")
# date, details (merchant + its category), amount Cr/Dr, then the cashback-earned column we don't need
AXIS_ROW = re.compile(rf"^(\d{{2}}/\d{{2}}/\d{{4}}) (.+?) {_AMT} (Cr|Dr) [\d,]+\.\d{{2}} (?:Cr|Dr)$")


def parse_axis_card(path: str, password: str) -> list:
    with pdfplumber.open(path, password=password) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)

    period = AXIS_PERIOD.search(full)
    st = Statement(
        source_file=path,
        account=f"AXIS CC {AXIS_CARD.search(full).group(1)}",
        period_start=datetime.strptime(period.group(1), "%d/%m/%Y").date(),
        period_end=datetime.strptime(period.group(2), "%d/%m/%Y").date(),
    )
    table = full.split("Account Summary", 1)[-1].split("End of Statement", 1)[0]
    for line in table.splitlines():
        if m := AXIS_ROW.match(line.strip()):
            st.transactions.append(Transaction(
                date=datetime.strptime(m.group(1), "%d/%m/%Y").date(),
                account=st.account,
                raw_desc=m.group(2),
                amount=money(m.group(3)),
                direction="credit" if m.group(4) == "Cr" else "debit",
                source_file=path,
            ))

    s = AXIS_SUMMARY.search(full)
    prev = -money(s.group(1)) if s.group(2) == "Cr" else money(s.group(1))
    payments, credits, purchases, cash, other = (money(s.group(i)) for i in range(3, 8))
    due = -money(s.group(8)) if s.group(9) == "Cr" else money(s.group(8))
    debits = sum(t.amount for t in st.transactions if t.direction == "debit")
    creds = sum(t.amount for t in st.transactions if t.direction == "credit")
    st.checks += [
        _check(f"{st.account}: purchases & charges", purchases + cash + other, debits),
        _check(f"{st.account}: payments & credits", payments + credits, creds),
        _check(f"{st.account}: previous + charges - credits = total due", due, prev + debits - creds),
    ]
    return [st]


# ---------------------------------------------------------------- folders

# ---------------------------------------------------------------- HDFC bank (Combined Email Statement)

HDFC_BANK_ACCOUNT = re.compile(r"Account Number : \d*(\d{4})\b")
HDFC_BANK_FROM = re.compile(r"Statement From : (\d{2}/\d{2}/\d{4}) To (\d{2}/\d{2}/\d{4})")
HDFC_BANK_OPENING = re.compile(r"Opening Balance : ([\d,]+\.\d{2})")
# date, narration, withdrawal, deposit, closing balance; anything else continues the row above
# (including "11/09/2026 Ref ..." value-date lines, which also start with a date)
HDFC_BANK_ROW = re.compile(r"^(\d{2}/\d{2}/\d{4}) (.*?) ?([\d,]+\.\d{2}) ([\d,]+\.\d{2}) ([\d,]+\.\d{2})$")
HDFC_BANK_SUMMARY = re.compile(r"^([\d,]+\.\d{2}) ([\d,]+\.\d{2}) ([\d,]+\.\d{2}) ([\d,]+\.\d{2})$")


def parse_hdfc_bank(path: str, password: str) -> list:
    """One PDF holds every savings account of the customer ID; returns one Statement per account."""
    statements, st, in_table, last, expect_summary = {}, None, False, None, False
    with pdfplumber.open(path, password=password) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                line = line.strip()
                if m := HDFC_BANK_ACCOUNT.search(line):
                    acct = f"HDFC SB {m.group(1)}"
                    st = statements.get(acct)
                    in_table, last = False, None
                    continue
                if st is None and (m := HDFC_BANK_FROM.search(line)):
                    st = statements[acct] = Statement(
                        source_file=path, account=acct,
                        period_start=datetime.strptime(m.group(1), "%d/%m/%Y").date(),
                        period_end=datetime.strptime(m.group(2), "%d/%m/%Y").date())
                    st.opening = st.balance = None
                    continue
                if st is None:
                    continue
                if st.opening is None and (m := HDFC_BANK_OPENING.search(line)):
                    st.opening = st.balance = money(m.group(1))
                    continue
                if line.startswith("Txn Date Narration"):
                    in_table = True
                    continue
                if line == "SUMMARY":
                    in_table = False
                    continue
                if line.startswith("Opening Balance Debit Amount"):
                    expect_summary = True
                    continue
                if expect_summary and (m := HDFC_BANK_SUMMARY.match(line)):
                    expect_summary = False
                    debits = sum(t.amount for t in st.transactions if t.direction == "debit")
                    credits = sum(t.amount for t in st.transactions if t.direction == "credit")
                    st.checks.append(_check(f"{st.account}: withdrawals", money(m.group(2)), debits))
                    st.checks.append(_check(f"{st.account}: deposits", money(m.group(3)), credits))
                    st.checks.append(_check(f"{st.account}: opening + deposits - withdrawals = closing",
                                            money(m.group(4)), money(m.group(1)) + credits - debits))
                    continue
                if not in_table:
                    continue
                if m := HDFC_BANK_ROW.match(line):
                    out, inn, balance = money(m.group(3)), money(m.group(4)), money(m.group(5))
                    if abs(st.balance - out + inn - balance) >= 0.01:
                        st.checks.append(_check(f"{st.account}: running balance on {m.group(1)}",
                                                balance, st.balance - out + inn))
                    st.balance = balance
                    last = Transaction(
                        date=datetime.strptime(m.group(1), "%d/%m/%Y").date(), account=st.account,
                        raw_desc=m.group(2), amount=out or inn, direction="debit" if out else "credit",
                        source_file=path)
                    st.transactions.append(last)
                elif last is not None:
                    last.raw_desc = f"{last.raw_desc} {line}"
    for st in statements.values():
        del st.balance
    # Value-date and reference tails make every description unique without helping categorization
    for st in statements.values():
        for t in st.transactions:
            t.raw_desc = re.sub(r"\s*Value\s+Dt\b.*$", "", t.raw_desc).strip()
    return list(statements.values())


PARSERS = {
    "icici-bank": parse_icici_bank,
    "hdfc-bank": parse_hdfc_bank,
    "icici-credit-card": parse_icici_card,
    "sbi-bank": parse_sbi_bank,
    "yes-credit-card": parse_yes_card,
    "kotak-credit-card": parse_kotak_card,
    "hdfc-credit-card": parse_hdfc_card,
    "axis-credit-card": parse_axis_card,
}


def parse_folder(root: str, password_for):
    """password_for(folder) returns the PDF password for a statements/ folder ("icici-bank", "hdfc-credit-card", ...).
    Returns (statements, skipped). skipped holds (what, reason) for folders with no parser, PDFs that
    aren't statements the parser recognises, and second copies of a statement already read."""
    from pathlib import Path

    statements, skipped, seen = [], [], set()
    for folder in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        pdfs = sorted(folder.glob("*.pdf"))
        parser = PARSERS.get(folder.name)
        if parser is None:
            if pdfs:
                skipped.append((f"statements/{folder.name}", f"{len(pdfs)} PDF(s), no parser for this bank yet"))
            continue
        password = password_for(folder.name)
        for pdf in pdfs:
            # Same password, different case: ICICI cards take it in lower case, its emailed bank statements in upper case
            for attempt in dict.fromkeys([password, password.upper()]):
                try:
                    parsed = parser(str(pdf), attempt)
                    break
                except Exception as e:  # not a statement this parser knows (e.g. a fee advice), or a wrong password
                    error = e
            else:
                skipped.append((f"{folder.name}/{pdf.name}", f"could not read as a statement ({type(error).__name__})"))
                continue
            for st in parsed:
                key = (st.account, st.period_start, st.period_end)
                if key in seen:  # the same statement saved twice (e.g. dropped in by hand and fetched from Gmail)
                    skipped.append((f"{folder.name}/{pdf.name}", "duplicate of a statement already read"))
                    continue
                seen.add(key)
                statements.append(st)

    # A monthly download whose days all sit inside a longer emailed statement for the same account
    # would count those days twice: keep the longer one.
    kept = []
    for st in statements:
        outer = next((o for o in statements if o is not st and o.account == st.account
                      and o.period_start <= st.period_start and st.period_end <= o.period_end), None)
        if outer:
            src, by = Path(st.source_file), Path(outer.source_file)
            skipped.append((f"{src.parent.name}/{src.name} ({st.account})",
                            f"duplicate: its days are inside {by.name}"))
        else:
            kept.append(st)
    return kept, skipped
