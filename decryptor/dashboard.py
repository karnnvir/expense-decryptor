"""Write out/dashboard.html: spend by month, a category breakdown for the chosen month, and one category's trend.

One self-contained page: the data is embedded as JSON, Chart.js comes from cdnjs. Open it in a browser."""
import calendar
import json
from collections import defaultdict
from datetime import date

import yaml

COMPLETE_AT = 0.97  # share of account-days covered for a month to count as complete (a bank skipping a day is fine)


def load_groups(rules_path, categories):
    groups = yaml.safe_load(open(rules_path)).get("groups") or {}
    grouped = {c for cats in groups.values() for c in cats}
    other = [c for c in categories if c not in grouped]
    return {**groups, **({"Other": other} if other else {})}


def month_coverage(statements, y, m):
    """(share of account-days covered, gaps) for one month, across every account in the folder."""
    spans = defaultdict(list)
    for st in statements:
        spans[st.account].append((st.period_start, st.period_end))
    days = calendar.monthrange(y, m)[1]
    covered_total, gaps = 0, []
    name = date(y, m, 1).strftime("%b")
    for account, periods in sorted(spans.items()):
        covered = [d for d in range(1, days + 1) if any(lo <= date(y, m, d) <= hi for lo, hi in periods)]
        covered_total += len(covered)
        if not covered:
            gaps.append(f"{account}: no statement")
        elif len(covered) < days:
            missing = sorted(set(range(1, days + 1)) - set(covered))
            gaps.append(f"{account}: {name} {covered[0]}–{covered[-1]}"
                        + (f", missing {name} {', '.join(map(str, missing))}"
                           if len(missing) <= 3 else " only"))
    return covered_total / (days * len(spans)), gaps


def build_data(statements, transactions, categories, groups):
    spend = [t for t in transactions if t.kind in ("spend", "refund")]
    keys = sorted({t.date.strftime("%Y-%m") for t in spend})
    months, (y, m) = [], (int(keys[0][:4]), int(keys[0][5:]))
    while f"{y}-{m:02d}" <= keys[-1]:  # fill gaps so the axis is continuous
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    index = {f"{y}-{m:02d}": i for i, (y, m) in enumerate(months)}
    by_cat = defaultdict(lambda: [0.0] * len(months))
    for t in spend:
        by_cat[t.category][index[t.date.strftime("%Y-%m")]] += t.amount if t.direction == "debit" else -t.amount

    month_rows = []
    for i, (y, m) in enumerate(months):
        share, gaps = month_coverage(statements, y, m)
        month_rows.append({
            "label": date(y, m, 1).strftime("%b %Y"),
            "short": date(y, m, 1).strftime("%b %y" if len(months) > 12 else "%b"),  # more than a year has two Aprils
            "total": round(sum(v[i] for v in by_cat.values()), 2),
            "coverage": round(share, 3),
            "complete": share >= COMPLETE_AT,
            "gaps": gaps,
        })
    return {
        "built": date.today().strftime("%d %b %Y"),
        "statements": len({st.source_file for st in statements}),
        "months": month_rows,
        "categories": {c: [round(v, 2) for v in vals] for c, vals in by_cat.items()},
        "groups": {g: [c for c in cats if c in by_cat] for g, cats in groups.items()},
    }


def write(path, statements, transactions, categories, rules_path):
    data = build_data(statements, transactions, categories, load_groups(rules_path, categories))
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("__DATA__", payload))
    return data


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expense Decryptor</title>
<style>
:root {
  --bg: #f7f6f3; --card: #ffffff; --tile: #f0efea; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --hair: rgba(11,11,11,0.10); --grid: #e1e0d9; --bar: #2a78d6; --bar-soft: #a9c8ef; --select: #0b0b0b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141413; --card: #1a1a19; --tile: #232321; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --hair: rgba(255,255,255,0.10); --grid: #2c2c2a; --bar: #3987e5; --bar-soft: #284b73; --select: #f0efec;
  }
}
:root[data-theme="dark"] {
  --bg: #141413; --card: #1a1a19; --tile: #232321; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --hair: rgba(255,255,255,0.10); --grid: #2c2c2a; --bar: #3987e5; --bar-soft: #284b73; --select: #f0efec;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 880px; margin: 0 auto; padding: 28px 16px 48px; }
h1 { font-size: 22px; font-weight: 600; margin: 0; }
h2 { font-size: 16px; font-weight: 600; margin: 0; }
.sub { color: var(--ink-2); margin: 4px 0 20px; font-size: 14px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.tile { background: var(--tile); border-radius: 8px; padding: 12px 14px; }
.tile p { margin: 0; }
.tile .l { font-size: 13px; color: var(--ink-2); }
.tile .v { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; margin-top: 2px; }
.tile .n { font-size: 12px; color: var(--muted); margin-top: 2px; }
section { background: var(--card); border: 1px solid var(--hair); border-radius: 12px; padding: 16px 16px 12px; margin-top: 16px; }
.hd { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.legend { display: flex; gap: 14px; font-size: 12px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.sw { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.seg { display: inline-flex; border: 1px solid var(--hair); border-radius: 8px; overflow: hidden; }
.seg button { font: inherit; font-size: 13px; padding: 5px 12px; border: 0; background: transparent; color: var(--ink-2); cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--tile); color: var(--ink); font-weight: 600; }
select { font: inherit; font-size: 14px; padding: 5px 8px; border-radius: 8px; border: 1px solid var(--hair); background: var(--card); color: var(--ink); max-width: 100%; }
.chart { position: relative; width: 100%; }
.note { font-size: 13px; color: var(--ink-2); margin: 0 0 8px; }
.foot { font-size: 12px; color: var(--muted); margin-top: 16px; }
details { margin-top: 8px; font-size: 13px; color: var(--ink-2); }
summary { cursor: pointer; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; font-variant-numeric: tabular-nums; }
td, th { padding: 4px 6px; border-bottom: 1px solid var(--hair); text-align: right; font-weight: 400; }
td:first-child, th:first-child { text-align: left; }
th { color: var(--muted); }
#nochart { display: none; color: var(--ink-2); }
</style>
</head>
<body>
<main>
<h1>Expense Decryptor</h1>
<p class="sub" id="sub"></p>

<div class="tiles">
  <div class="tile"><p class="l" id="k1l">Spend</p><p class="v" id="k1"></p><p class="n" id="k1n"></p></div>
  <div class="tile"><p class="l">Biggest category</p><p class="v" id="k2"></p><p class="n" id="k2n"></p></div>
  <div class="tile"><p class="l">Data coverage</p><p class="v" id="k3"></p><p class="n" id="k3n"></p></div>
</div>
<p id="nochart">The charts need an internet connection to load Chart.js.</p>

<section>
  <div class="hd">
    <h2>Spend by month</h2>
    <div class="legend"><span><i class="sw" style="background:var(--bar)"></i>Complete month</span><span><i class="sw" style="background:var(--bar-soft)"></i>Partial data</span></div>
  </div>
  <p class="note">Click a month to see where the money went.</p>
  <div class="chart" style="height:240px"><canvas id="cMonth" role="img" aria-label="Column chart of total spend per month"></canvas></div>
</section>

<section>
  <div class="hd">
    <h2>Where it went in <span id="selMonth"></span></h2>
    <div class="seg" role="group" aria-label="Breakdown level">
      <button id="bGroups" aria-pressed="true">Groups</button><button id="bCats" aria-pressed="false">All categories</button>
    </div>
  </div>
  <p class="note">Click a bar to see its trend below.</p>
  <div class="chart" id="breakWrap"><canvas id="cBreak" role="img" aria-label="Horizontal bar chart of spend by category for the selected month"></canvas></div>
  <details><summary>See the numbers</summary><table id="breakTable"></table></details>
</section>

<section>
  <div class="hd">
    <h2>Trend</h2>
    <select id="pick" aria-label="Group or category to show"></select>
  </div>
  <p class="note" id="trendNote"></p>
  <div class="chart" style="height:240px"><canvas id="cTrend" role="img" aria-label="Column chart of the chosen category's spend per month"></canvas></div>
</section>

<p class="foot">Spend is net of refunds. Card payments, transfers between your own accounts, investments and income are left out. Light bars mean some accounts are missing for part of that month; hover a month to see which.</p>
</main>

<script>const DATA = __DATA__;</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
(function () {
  const M = DATA.months, CAT = DATA.categories, GRP = DATA.groups;
  const n = M.length;
  const SERIES = Object.assign({}, CAT);
  for (const g in GRP) SERIES[g] = Array.from({length: n}, (_, i) => GRP[g].reduce((s, c) => s + CAT[c][i], 0));
  const catNames = Object.keys(CAT).sort();
  const inr = v => (v < 0 ? "-" : "") + new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 0}).format(Math.abs(Math.round(v)));
  const axis = v => { const a = Math.abs(v), s = v < 0 ? "-" : "";
    return a >= 100000 ? s + "₹" + (a / 100000).toFixed(a >= 1000000 ? 0 : 1) + "L" : a >= 1000 ? s + "₹" + Math.round(a / 1000) + "k" : s + "₹" + a; };
  const $ = id => document.getElementById(id);

  const lastComplete = M.map(m => m.complete).lastIndexOf(true);
  let mi = lastComplete >= 0 ? lastComplete : n - 1, level = "groups", pick = null;

  $("sub").textContent = `${M[0].label} to ${M[n - 1].label} · built ${DATA.built} from ${DATA.statements} statement PDFs`;
  if (typeof Chart === "undefined") { $("nochart").style.display = "block"; return; }

  function colors() {
    const s = getComputedStyle(document.documentElement), v = k => s.getPropertyValue(k).trim();
    return {bar: v("--bar"), soft: v("--bar-soft"), grid: v("--grid"), muted: v("--muted"), select: v("--select"), card: v("--card")};
  }
  let C = colors();
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  const tip = {callbacks: {label: c => inr(c.raw)}};
  const base = {responsive: true, maintainAspectRatio: false, animation: {duration: 250},
    plugins: {legend: {display: false}, tooltip: tip}};
  const yScale = () => ({grid: {color: C.grid}, border: {display: false}, ticks: {color: C.muted, callback: axis, maxTicksLimit: 6}});
  const xScale = () => ({grid: {display: false}, border: {color: C.grid}, ticks: {color: C.muted, autoSkipPadding: 8, maxRotation: 0}});
  const column = {borderRadius: {topLeft: 4, topRight: 4}, borderSkipped: "bottom", maxBarThickness: 28};
  const pointer = (e, a) => { e.native.target.style.cursor = a.length ? "pointer" : "default"; };
  const monthTip = {callbacks: {
    label: c => inr(c.raw),
    afterLabel: c => { const m = M[c.dataIndex], pct = Math.floor(m.coverage * 100) + "% of account-days covered";
      return m.gaps.length ? [pct, ...m.gaps.slice(0, 4), ...(m.gaps.length > 4 ? [`and ${m.gaps.length - 4} more`] : [])] : [pct]; }}};

  const cMonth = new Chart($("cMonth"), {type: "bar",
    data: {labels: M.map(m => m.short), datasets: [{data: M.map(m => m.total), ...column}]},
    options: {...base, scales: {y: yScale(), x: xScale()}, plugins: {...base.plugins, tooltip: monthTip},
      onHover: pointer, onClick: (e, a) => { if (a.length) { mi = a[0].index; pick = null; draw(); } }}});

  const cBreak = new Chart($("cBreak"), {type: "bar",
    data: {labels: [], datasets: [{data: [], borderRadius: {topRight: 4, bottomRight: 4}, borderSkipped: "left", maxBarThickness: 20}]},
    options: {...base, indexAxis: "y", scales: {x: yScale(), y: {grid: {display: false}, border: {display: false}, ticks: {color: C.muted, autoSkip: false}}},
      onHover: pointer, onClick: (e, a) => { if (a.length) { pick = cBreak.data.labels[a[0].index]; draw(); } }}});

  const cTrend = new Chart($("cTrend"), {type: "bar",
    data: {labels: M.map(m => m.short), datasets: [{data: [], ...column}]},
    options: {...base, scales: {y: yScale(), x: xScale()}, plugins: {...base.plugins, tooltip: monthTip},
      onHover: pointer, onClick: (e, a) => { if (a.length) { mi = a[0].index; draw(); } }}});

  const sel = $("pick");
  sel.innerHTML = '<optgroup label="Groups">' + Object.keys(GRP).map(g => `<option>${g}</option>`).join("") +
    '</optgroup><optgroup label="Categories">' + catNames.map(c => `<option>${c}</option>`).join("") + "</optgroup>";
  sel.addEventListener("change", () => { pick = sel.value; draw(); });

  function setLevel(l) { level = l; pick = null;
    $("bGroups").setAttribute("aria-pressed", l === "groups"); $("bCats").setAttribute("aria-pressed", l === "cats"); draw(); }
  $("bGroups").addEventListener("click", () => setLevel("groups"));
  $("bCats").addEventListener("click", () => setLevel("cats"));

  function draw() {
    const m = M[mi];
    const names = level === "groups" ? Object.keys(GRP) : catNames;
    const rows = names.map(k => [k, SERIES[k][mi]]).filter(r => Math.round(r[1]) > 0).sort((a, b) => b[1] - a[1]);
    if (!pick || !(pick in SERIES)) pick = rows.length ? rows[0][0] : names[0];

    const ds = cMonth.data.datasets[0];
    ds.backgroundColor = M.map(x => x.complete ? C.bar : C.soft);
    ds.borderColor = M.map((_, i) => i === mi ? C.select : "transparent");
    ds.borderWidth = M.map((_, i) => i === mi ? 2 : 0);
    cMonth.update();

    $("breakWrap").style.height = Math.max(140, rows.length * 30 + 40) + "px";
    cBreak.data.labels = rows.map(r => r[0]);
    cBreak.data.datasets[0].data = rows.map(r => r[1]);
    cBreak.data.datasets[0].backgroundColor = rows.map(r => r[0] === pick ? C.bar : C.soft);
    cBreak.resize(); cBreak.update();
    $("breakTable").innerHTML = "<tr><th>" + (level === "groups" ? "Group" : "Category") + "</th><th>Amount</th><th>Share</th></tr>" +
      rows.map(r => `<tr><td>${r[0]}</td><td>${inr(r[1])}</td><td>${m.total ? Math.round(r[1] / m.total * 100) : 0}%</td></tr>`).join("") +
      `<tr><td>Total</td><td>${inr(m.total)}</td><td></td></tr>`;

    $("selMonth").textContent = m.label;
    $("k1l").textContent = "Spend in " + m.label;
    $("k1").textContent = inr(m.total);
    const done = M.filter(x => x.complete);
    $("k1n").textContent = done.length > 1 ? "Average of complete months: " + inr(done.reduce((s, x) => s + x.total, 0) / done.length) : "";
    const top = catNames.map(c => [c, CAT[c][mi]]).sort((a, b) => b[1] - a[1])[0];
    $("k2").textContent = top[0];
    $("k2n").textContent = inr(top[1]) + (m.total ? ` · ${Math.round(top[1] / m.total * 100)}% of the month` : "");
    $("k3").textContent = Math.floor(m.coverage * 100) + "%";
    $("k3n").textContent = m.complete ? "Complete month" : `Partial: ${m.gaps.length} account${m.gaps.length === 1 ? "" : "s"} missing days`;

    sel.value = pick;
    const s = SERIES[pick], active = s.filter(v => Math.round(v) !== 0).length;
    cTrend.data.datasets[0].data = s;
    cTrend.data.datasets[0].backgroundColor = M.map(x => x.complete ? C.bar : C.soft);
    cTrend.data.datasets[0].borderColor = M.map((_, i) => i === mi ? C.select : "transparent");
    cTrend.data.datasets[0].borderWidth = M.map((_, i) => i === mi ? 2 : 0);
    cTrend.update();
    $("trendNote").textContent = `${pick}: ${inr(s[mi])} in ${m.label}. ${inr(s.reduce((a, b) => a + b, 0))} across ${n} months, with spend in ${active} of them.`;
  }

  function restyle() {
    C = colors();
    for (const ch of [cMonth, cBreak, cTrend]) {
      for (const ax of Object.values(ch.options.scales)) {
        ax.ticks.color = C.muted;
        if (ax.grid.display !== false) ax.grid.color = C.grid;
        if (ax.border && ax.border.color) ax.border.color = C.grid;
      }
    }
    draw();
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", restyle);
  draw();
})();
</script>
</body>
</html>
"""
