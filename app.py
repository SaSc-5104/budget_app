"""Budget Tracker: browser-side budget analysis with responsive SVG charts."""
import html
import csv
import io
import json
from urllib.parse import quote
from js import window
import pandas as pd
import numpy as np
from pyscript import document
from pyodide.ffi import create_proxy

# category -> 50/30/20 group
GROUPS = {"Housing": "Needs", "Groceries": "Needs", "Utilities": "Needs", "Transport": "Needs",
          "Healthcare": "Needs", "Insurance": "Needs", "Debt payments": "Needs",
          "Dining out": "Wants", "Entertainment": "Wants", "Shopping": "Wants",
          "Subscriptions": "Wants", "Travel": "Wants", "Other": "Wants",
          "Savings & investing": "Savings"}
TARGET = {"Needs": 50, "Wants": 30, "Saved": 20}
COLORS = {"Needs": "#6D8196", "Wants": "#4A4A4A", "Saved": "#8A9A83"}
MAX_AMOUNT = 1_000_000_000
expenses = []  # list of {"category": str, "amount": float}
step = "dashboard"


def el(i):
    return document.getElementById(i)


def money(v):
    return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"


def parse_positive(raw, name):
    """Return a positive float or raise ValueError with a user-friendly message."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a number for {name}.")
    if not np.isfinite(v) or v <= 0:
        raise ValueError(f"{name.capitalize()} must be greater than 0.")
    if v > MAX_AMOUNT:
        raise ValueError(f"{name.capitalize()} is too large.")
    return round(v, 2)


# ---------- processing ----------
def analyze(income, items):
    df = pd.DataFrame(items, columns=["category", "amount"])
    df["group"] = df["category"].map(GROUPS).fillna("Wants")
    by_cat = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    grp = df.groupby("group")["amount"].sum()
    total = float(df["amount"].sum())
    left = income - total
    needs, wants = float(grp.get("Needs", 0)), float(grp.get("Wants", 0))
    saved_cat = float(grp.get("Savings", 0))
    saved = saved_cat + max(left, 0)  # money not spent counts as saved
    return {"by_cat": by_cat, "total": total, "left": left, "needs": needs, "wants": wants,
            "saved_cat": saved_cat, "saved": saved, "income": income,
            "pct": {"Needs": needs / income * 100, "Wants": wants / income * 100, "Saved": saved / income * 100}}


def recommendations(a, monthly=True):
    inc, recs = a["income"], []
    by_cat = a["by_cat"]
    wants_by_cat = by_cat[[c for c in by_cat.index if GROUPS.get(c, "Wants") == "Wants"]]
    if a["left"] < 0:
        recs.append(("warn", f"You are spending {money(-a['left'])} more than your income for this period. Start with your largest 'wants' category"
                     + (f", {wants_by_cat.index[0]} ({money(wants_by_cat.iloc[0])})." if len(wants_by_cat) else ".")))
    save_pct = a["pct"]["Saved"]
    if a["left"] >= 0 and save_pct < 20:
        recs.append(("tip", f"You save {save_pct:.0f}% of your income. Putting aside {money(inc * 0.2 - a['saved'])} more would bring you to the 20% guideline."))
    if a["pct"]["Needs"] > 50:
        recs.append(("warn" if a["pct"]["Needs"] > 60 else "tip", f"Essentials take {a['pct']['Needs']:.0f}% of income (guideline: 50%). Review your biggest need, {by_cat[[c for c in by_cat.index if GROUPS.get(c) == 'Needs']].index[0]}, for savings such as a cheaper plan or provider."))
    if a["pct"]["Wants"] > 30 and len(wants_by_cat):
        cut = a["wants"] - inc * 0.3
        recs.append(("tip", f"Wants are {a['pct']['Wants']:.0f}% of income (guideline: 30%). Trimming {money(cut)} from this period's budget, starting with {wants_by_cat.index[0]}, would bring you in line."))
    if "Housing" in by_cat.index and by_cat["Housing"] / inc > 0.35:
        recs.append(("warn", f"Housing is {by_cat['Housing'] / inc:.0%} of income. Above about 30-35% it tends to squeeze the rest of your budget."))
    needs_total = a["needs"]
    if monthly and a["saved"] > 0 and needs_total > 0:
        months = 3 * needs_total / a["saved"]
        recs.append(("tip", f"An emergency fund of 3 months of essentials is {money(3 * needs_total)}. At your current saving rate you would reach it in about {months:.0f} months."))
    if save_pct >= 20 and a["pct"]["Wants"] <= 30 and a["left"] >= 0:
        recs.insert(0, ("good", f"Nice work: you are saving {save_pct:.0f}% of your income and keeping wants at {a['pct']['Wants']:.0f}%."))
    return recs


def budget_insights(a):
    positive, negative = [], []
    if a["left"] >= 0:
        positive.append(f"Income covers expenses, with {money(a['left'])} left over.")
    else:
        negative.append(f"Expenses exceed income by {money(-a['left'])}.")
    if a["pct"]["Needs"] <= 50:
        positive.append(f"Needs use {a['pct']['Needs']:.0f}% of income, within the 50% guideline.")
    else:
        negative.append(f"Needs use {a['pct']['Needs']:.0f}% of income, above the 50% guideline.")
    if a["pct"]["Wants"] <= 30:
        positive.append(f"Wants use {a['pct']['Wants']:.0f}% of income, within the 30% guideline.")
    else:
        negative.append(f"Wants use {a['pct']['Wants']:.0f}% of income, above the 30% guideline.")
    if a["pct"]["Saved"] >= 20:
        positive.append(f"Savings and unspent income reach {a['pct']['Saved']:.0f}%, meeting the 20% guideline.")
    else:
        negative.append(f"Savings are {a['pct']['Saved']:.0f}% of income, below the 20% guideline.")
    if not positive:
        positive.append("This report gives you a clear starting point for your budget.")
    if not negative:
        negative.append("No overspending or 50/30/20 guideline gaps were found.")
    return positive, negative


# ---------- charts: responsive, native SVG ----------
PALETTE = ["#6D8196", "#8A9A83", "#A88D70", "#4A4A4A", "#B5A88D", "#8799A8", "#CBCBCB"]


def chart_empty():
    return '<div class="chart-empty"><span class="chart-empty-mark" aria-hidden="true">—</span><strong>No expense data yet</strong><span>Add an expense to see your spending breakdown.</span></div>'


def chart_donut(a):
    vals = list(a["by_cat"].items())
    if not vals or a["total"] <= 0:
        return chart_empty()
    if len(vals) > 6:
        vals = vals[:6] + [("Everything else", sum(v for _, v in vals[6:]))]
    total, circumference, offset = sum(v for _, v in vals), 2 * np.pi * 42, 0
    arcs, legend = [], []
    for i, (name, value) in enumerate(vals):
        pct = value / total if total else 0
        dash = circumference * pct
        color = PALETTE[i % len(PALETTE)]
        arcs.append(f'<circle cx="60" cy="60" r="42" fill="none" stroke="{color}" stroke-width="13" stroke-dasharray="{dash:.2f} {circumference-dash:.2f}" stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 60 60)"/>')
        offset += dash
        legend.append(f'<div class="chart-legend-row"><i style="background:{color}"></i><span>{html.escape(str(name))}</span><b>{money(value)}</b></div>')
    return '<div class="donut-layout"><svg class="donut" viewBox="0 0 120 120" role="img" aria-label="Spending distribution">' + ''.join(arcs) + f'<text x="60" y="57" text-anchor="middle" class="donut-total">{html.escape(money(a["total"]))}</text><text x="60" y="70" text-anchor="middle" class="donut-caption">total spent</text></svg><div class="chart-legend">' + ''.join(legend) + '</div></div>'


def chart_rule(a):
    rows = []
    for key, target in TARGET.items():
        actual = a["pct"][key]
        color = COLORS[key]
        rows.append(f'<div class="compare-row"><div class="compare-label"><span>{key}</span><b>{actual:.0f}%</b></div><div class="track"><i style="width:{min(actual,100):.1f}%;background:{color}"></i><em style="left:{target}%" title="Guideline: {target}%"></em></div><div class="compare-foot"><span>You</span><span>Guideline {target}%</span></div></div>')
    return '<div class="compare-chart">' + ''.join(rows) + '</div>'


def chart_cats(a):
    s = (a["by_cat"] / a["income"] * 100).sort_values(ascending=False).head(7)
    if s.empty:
        return chart_empty()
    rows = []
    for category, value in s.items():
        color = COLORS["Saved"] if GROUPS.get(category) == "Savings" else COLORS["Needs"] if GROUPS.get(category) == "Needs" else COLORS["Wants"]
        rows.append(f'<div class="category-row"><div class="category-label"><span>{html.escape(str(category))}</span><b>{value:.0f}%</b></div><div class="track"><i style="width:{min(value,100):.1f}%;background:{color}"></i></div></div>')
    return '<div class="category-chart">' + ''.join(rows) + '</div>'


# ---------- rendering ----------
def render():
    el("dashboard-step").hidden = step != "dashboard"
    for name in ("income", "expense", "results"):
        el(f"{name}-step").hidden = step != name
    el("progress").hidden = step == "dashboard"
    if step == "dashboard":
        return
    for i in range(1, 4):
        el(f"p{i}").className = "active" if i == {"income": 1, "expense": 2, "results": 3}[step] else ""


def show_results():
    global expenses, step
    try:
        income = parse_positive(el("income").value, "income")
        entries = []
        for i, category in enumerate(GROUPS):
            raw = el(f"expense-{i}").value.strip()
            if not raw:
                continue
            try:
                amount = float(raw)
            except ValueError:
                raise ValueError(f"Enter a valid amount for {category}.")
            if not np.isfinite(amount) or amount < 0 or amount > MAX_AMOUNT:
                raise ValueError(f"Enter an amount from 0 to {money(MAX_AMOUNT)} for {category}.")
            if amount > 0:
                entries.append({"category": category, "amount": round(amount, 2)})
    except ValueError as ex:
        el("expense-err").textContent = str(ex)
        return
    el("expense-err").textContent = ""
    expenses = entries
    a = analyze(income, expenses)
    big = el("big")
    if a["left"] >= 0:
        big.className, big.textContent = "big", f"{money(a['left'])} left"
        el("bigsub").textContent = f"of {money(income)}"
    else:
        big.className, big.textContent = "big bad", f"{money(-a['left'])} over"
        el("bigsub").textContent = f"Expenses of {money(a['total'])} exceed your income of {money(income)}."
    scale = max(income, a["total"], 1)
    parts = [("Needs", a["needs"], COLORS["Needs"]), ("Wants", a["wants"], COLORS["Wants"]),
             ("Savings", a["saved_cat"], COLORS["Saved"]), ("Unspent", max(a["left"], 0), "#CBCBCB")]
    el("split").innerHTML = "".join(f"<div style='width:{v / scale * 100:.2f}%;background:{c}'></div>" for _, v, c in parts)
    el("legend").innerHTML = "".join(f"<span><i class='dot' style='background:{c}'></i>{n} {money(v)}</span>" for n, v, c in parts)
    positive, negative = budget_insights(a)
    el("positives").innerHTML = "".join(f"<li>{html.escape(t)}</li>" for t in positive)
    el("negatives").innerHTML = "".join(f"<li>{html.escape(t)}</li>" for t in negative)
    el("recs").innerHTML = "".join(f"<li class='{k}'>{html.escape(t)}</li>" for k, t in recommendations(a))
    el("c1").innerHTML, el("c2").innerHTML, el("c3").innerHTML = chart_donut(a), chart_rule(a), chart_cats(a)
    step = "results"
    render()


# ---------- events ----------
def on_income_submit(e):
    global step
    e.preventDefault()
    try:
        parse_positive(el("income").value, "income")
    except ValueError as ex:
        el("income-err").textContent = str(ex)
        return
    el("income-err").textContent = ""
    step = "expense"
    render()


def back_to_income(e):
    global step
    step = "income"
    render()


def begin_budget(e=None):
    global step
    el("income").value = ""
    el("income-err").textContent = ""
    el("expense-err").textContent = ""
    for i in range(len(GROUPS)):
        el(f"expense-{i}").value = ""
    step = "income"
    render()


def edit_expenses(e):
    global step
    step = "expense"
    render()


def start_over(e):
    global step, expenses
    step, expenses = "income", []
    el("income").value = ""
    for i in range(len(GROUPS)):
        el(f"expense-{i}").value = ""
    render()


def download_csv(e=None):
    income = parse_positive(el("income").value, "income")
    a = analyze(income, expenses)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Budget summary"])
    writer.writerow(["Report type", "Monthly budget"])
    writer.writerow(["Monthly income", f'{income:.2f}'])
    writer.writerow(["Total expenses", f'{a["total"]:.2f}'])
    writer.writerow(["Left over (negative means over budget)", f'{a["left"]:.2f}'])
    writer.writerow([])
    writer.writerow(["Budget group", "Spent", "Share of income", "Guideline"])
    for key, amount, target_key in (("Needs", a["needs"], "Needs"), ("Wants", a["wants"], "Wants"), ("Savings", a["saved_cat"], "Saved")):
        pct = amount / income * 100
        writer.writerow([key, f"{amount:.2f}", f"{pct:.1f}%", f'{TARGET[target_key]}%'])
    writer.writerow([])
    writer.writerow(["Category", "Group", "Monthly amount", "Share of income"])
    for item in expenses:
        writer.writerow([item["category"], GROUPS.get(item["category"], "Wants"), f'{item["amount"]:.2f}', f'{item["amount"] / income * 100:.1f}%'])
    anchor = document.createElement("a")
    anchor.href = "data:text/csv;charset=utf-8," + quote(output.getvalue())
    anchor.download = "budget-summary.csv"
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()


def pdf_report_data():
    income = parse_positive(el("income").value, "income")
    a = analyze(income, expenses)
    return {"monthly": True, "period": "Monthly budget",
            "income": income, "total": a["total"], "left": a["left"], "needs": a["needs"],
            "wants": a["wants"], "saved": a["saved"], "saved_category": a["saved_cat"],
            "categories": [{"name": item["category"], "amount": item["amount"],
                            "group": GROUPS.get(item["category"], "Wants")} for item in expenses]}


def download_pdf(e=None):
    window.downloadBudgetPdf(json.dumps(pdf_report_data()))


def build_expense_fields():
    groups = {"Needs": [], "Wants": [], "Savings": []}
    descriptions = {"Needs": "Core costs", "Wants": "Flexible spending", "Savings": "Saving and investing"}
    for category, group in GROUPS.items():
        groups[group].append(category)
    blocks = []
    category_index = {c: i for i, c in enumerate(GROUPS)}
    for group, categories in groups.items():
        fields = []
        for category in categories:
            idx = category_index[category]
            fields.append(f'<div class="category-field"><label for="expense-{idx}">{html.escape(category)}</label><input id="expense-{idx}" type="number" min="0" step="0.01" placeholder="$ 0" inputmode="decimal"></div>')
        blocks.append(f'<section class="category-group {group.lower()}"><h3>{group}<small>{descriptions[group]}</small></h3>{"".join(fields)}</section>')
    el("expense-fields").innerHTML = "".join(blocks)


build_expense_fields()
el("income-form").addEventListener("submit", create_proxy(on_income_submit))
el("start-budget").addEventListener("click", create_proxy(begin_budget))
el("back-income").addEventListener("click", create_proxy(back_to_income))
el("show-results").addEventListener("click", create_proxy(lambda e: show_results()))
el("edit-expenses").addEventListener("click", create_proxy(edit_expenses))
el("download-csv").addEventListener("click", create_proxy(download_csv))
el("download-pdf").addEventListener("click", create_proxy(download_pdf))
el("start-over").addEventListener("click", create_proxy(start_over))
el("loading").hidden = True
render()
