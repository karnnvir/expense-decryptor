# Expense Decryptor

Turns Indian bank and credit card statement PDFs into a monthly spend breakdown by category: an Excel workbook, an interactive dashboard and a JSON summary. It can fetch the PDFs from Gmail with read-only access.

Everything runs on your machine. Your statements, reports, passwords, Gmail tokens and personal rules are git-ignored, so they stay out of this repository.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/rules.example.yaml config/rules.yaml
cp config/gmail_sources.example.yaml config/gmail_sources.yaml
.venv/bin/python run.py --month 2026-09
```

Drop PDFs into `statements/icici-credit-card/` and `statements/icici-bank/`. Each bank's PDF password is asked for at the prompt (or read from `STATEMENT_PASSWORD_ICICI`, `STATEMENT_PASSWORD_SBI`, …) and is never saved. Output: `out/expense_breakdown_2026-09.xlsx` (leave out `--month` to report every month the statements touch).

A calendar month usually spans two card statements (ICICI's cycle runs 19th to 18th), so drop in both.

### Refresh

```bash
./refresh.sh             # fetch new statement emails (last 120 days), then rebuild everything
./refresh.sh --no-fetch  # rebuild from the PDFs already in statements/
```

Writes `out/expense_breakdown.xlsx` (all months), `out/dashboard.html` and `out/summary.json`, and prints each month's total, coverage and the payees no rule matched. Exits 1 if a statement fails its checks.

In Claude Code, the `/refresh` skill runs the same script, adds the steps that need judgment and publishes to Notion. Install it by copying [skills/refresh/SKILL.md](skills/refresh/SKILL.md) to `~/.claude/skills/refresh/SKILL.md` and filling in its `<placeholders>`. It runs the script, then asks you about the unmatched payees in one go, adds rules for your answers, reruns, reports what changed and updates the Notion page and its Monthly spend table.

## Statement folders

One folder per bank and type, named `<bank>-credit-card` or `<bank>-bank`. Put every card from that bank in its folder.

**Scope** is yours to set: only the senders in `config/gmail_sources.yaml` are fetched, and a rule can leave a whole account out of the totals (`account:` with `kind: ignored`).

| Folder | Parser |
|---|---|
| `icici-bank` | yes |
| `hdfc-bank` | yes |
| `icici-credit-card` | yes |
| `sbi-bank` | yes |
| `yes-credit-card` | yes |
| `kotak-credit-card` | yes |
| `hdfc-credit-card` | yes, monthly statements and the annual "Year End Statement & Summary" |
| `axis-credit-card` | yes |

Adding a bank means writing its parser against a real statement and registering it in `PARSERS` in `decryptor/parsers.py`.

## Gmail fetch

`fetch_gmail.py` downloads statement PDFs from Gmail into the right `statements/` folder (senders are in `config/gmail_sources.yaml`). It only asks for **read-only** access.

One-time setup:
1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Gmail API**.
2. Under *APIs & Services → OAuth consent screen*, set it up as *External*, and add your own address as a test user.
3. Under *Credentials*, create an **OAuth client ID** of type *Desktop app*, download the JSON, and save it as `config/gmail_credentials.json`.
4. Run `.venv/bin/python fetch_gmail.py --since 2026/08/01`. A browser window asks you to approve read-only access once.

While the OAuth app is in *Testing* status, Google drops the approval after 7 days and the next fetch opens the approval window again. Publishing the app (OAuth consent screen → *Publish app*) stops that.

After that, each run only downloads emails it hasn't seen. Statements already in the folders aren't double-counted: the parser skips a second copy of the same account and period.

Statement passwords live in `config/passwords.yaml` (git-ignored). Quote every key: YAML reads a bare `yes` as `true`.

## How it works

1. **Parse** (`decryptor/parsers.py`): one parser per statement format. Each statement is validated: bank rows against the running balance, card totals against the statement summary. Results are on the workbook's Checks tab.
2. **Categorize** (`config/rules.yaml`, `decryptor/categorize.py`): the first matching rule sets merchant, category and kind. Card payments, self-transfers, investments and income are kept out of spend. A credit to a spend merchant nets against its category as a refund.
3. **Reconcile**: a card credit that exactly matches an earlier debit to the same merchant (EMI conversions, failed orders) cancels both.
4. **Report** (`decryptor/report.py`): every Summary total is a SUMIFS formula over the Transactions tab. Change a row's category from the dropdown and the totals update.
5. **Dashboard** (`decryptor/dashboard.py`): `out/dashboard.html`, rebuilt on every run and always covering every month. Open it in a browser:
   spend by month (click one), where it went that month by group or by category (click a bar), and that group's or category's trend.
   Months where some account is missing days are drawn lighter; hover to see which. Groups are set under `groups:` in `config/rules.yaml`.
   Charts load Chart.js from cdnjs, so the page needs an internet connection; the data itself stays in the file.
6. **Summary** (`decryptor/summary.py`): `out/summary.json`, every month's total, coverage, categories and not-spend totals, plus the payees no rule matched. `/refresh` reads it.

## Teaching it

Rows it is unsure of are highlighted and listed on the Review tab. Answer those, add a rule to `config/rules.yaml`, and rerun.

## What stays private

`statements/`, `out/`, `config/rules.yaml`, `config/gmail_sources.yaml`, `config/passwords.yaml` and the Gmail credential files are git-ignored. Check `git status` before you commit, and keep real names, account numbers and passwords out of the `.example` files and code comments.
