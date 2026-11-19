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
GUIDELINES = {
    "50/30/20": {"Needs": 50, "Wants": 30, "Saved": 20},
    "60/20/20": {"Needs": 60, "Wants": 20, "Saved": 20},
    "70/20/10": {"Needs": 70, "Wants": 20, "Saved": 10},
}
# Original group colors: Needs #6D8196, Wants #4A4A4A, Saved #8A9A83.
COLORS = {"Needs": "#6D8196", "Wants": "#4A4A4A", "Saved": "#8A9A83"}
MAX_AMOUNT = 1_000_000_000
expenses = []  # list of {"category": str, "amount": float}
step = "dashboard"


def el(i):
    return document.getElementById(i)


def money(v):
    return f"${v:,.2f}"


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
    income_cents = int(round(income * 100))
    category_cents = {}
    group_cents = {"Needs": 0, "Wants": 0, "Savings": 0}
    for item in items:
        cents = int(round(item["amount"] * 100))
        category = item["category"]
        category_cents[category] = category_cents.get(category, 0) + cents
        group = GROUPS.get(category, "Wants")
        group_cents[group] = group_cents.get(group, 0) + cents
    by_cat = pd.Series({category: cents / 100 for category, cents in category_cents.items()}, dtype=float).sort_values(ascending=False)
    total_cents = sum(group_cents.values())
    left_cents = income_cents - total_cents
    total, left = total_cents / 100, left_cents / 100
    needs, wants = group_cents["Needs"] / 100, group_cents["Wants"] / 100
    saved_cat = group_cents["Savings"] / 100
    saved = (group_cents["Savings"] + max(left_cents, 0)) / 100  # money not spent counts as saved
    return {"by_cat": by_cat, "total": total, "left": left, "needs": needs, "wants": wants,
            "saved_cat": saved_cat, "saved": saved, "income": income,
            "pct": {"Needs": needs / income * 100, "Wants": wants / income * 100, "Saved": saved / income * 100}}


def current_guideline():
    name = str(el("guideline-model").value)
    return name, GUIDELINES.get(name, GUIDELINES["50/30/20"])


def spending_profile(a, targets):
    pct = a["pct"]
    if a["left"] < 0:
        return "Over budget", f"Your expenses are {money(-a['left'])} higher than your monthly income."
    if pct["Saved"] >= 35:
        return "Savings-focused", f"You save or leave unspent {pct['Saved']:.0f}% of your income, giving you room to build toward future goals."
    if pct["Needs"] > 60:
        return "Essentials-focused", f"Essentials use {pct['Needs']:.0f}% of your income, leaving less room for flexible spending and savings."
    if pct["Wants"] > 40:
        return "Lifestyle-focused", f"Flexible spending uses {pct['Wants']:.0f}% of your income and is the largest opportunity to adjust."
    if pct["Needs"] <= targets["Needs"] and pct["Wants"] <= targets["Wants"] and pct["Saved"] >= targets["Saved"]:
        return "Balanced", "Your spending is within the selected guideline across essentials, flexible spending, and savings."
    return "Finding your balance", f"Your largest share goes to {max((("essentials", pct['Needs']), ("flexible spending", pct['Wants']), ("savings", pct['Saved'])), key=lambda item: item[1])[0]}. Small adjustments can help bring your budget closer to your priorities."


def recommendations(a, targets, savings_goal=""):
    inc, recs = a["income"], []
    by_cat = a["by_cat"]
    wants_by_cat = by_cat[[c for c in by_cat.index if GROUPS.get(c, "Wants") == "Wants"]]
    if a["left"] < 0:
        recs.append(("warn", f"You are spending {money(-a['left'])} more than your income for this period. Start with your largest 'wants' category"
                     + (f", {wants_by_cat.index[0]} ({money(wants_by_cat.iloc[0])})." if len(wants_by_cat) else ".")))
    save_pct = a["pct"]["Saved"]
    if a["left"] >= inc * 0.10:
        recs.append(("tip", f"You have {money(a['left'])} left over ({a['left'] / inc:.0%} of your income). Consider putting some toward savings or investing for a future goal."))
    if a["left"] >= 0 and save_pct < targets["Saved"]:
        gap = inc * targets["Saved"] / 100 - a["saved"]
        recs.append(("tip", f"Savings and unspent income are {save_pct:.0f}% of income. Putting aside {money(gap)} more would reach the {targets['Saved']}% guideline."))
    need_categories = [c for c in by_cat.index if GROUPS.get(c) == "Needs"]
    if a["pct"]["Needs"] > targets["Needs"] and need_categories:
        biggest_need = need_categories[0]
        recs.append(("warn" if a["pct"]["Needs"] > targets["Needs"] + 10 else "tip", f"Essentials take {a['pct']['Needs']:.0f}% of income (guideline: {targets['Needs']}%). Review your biggest need, {biggest_need}, for savings such as a cheaper plan or provider."))
    if a["pct"]["Wants"] > targets["Wants"] and len(wants_by_cat):
        cut = a["wants"] - inc * targets["Wants"] / 100
        recs.append(("tip", f"Flexible spending is {a['pct']['Wants']:.0f}% of income (guideline: {targets['Wants']}%). Trimming {money(cut)} from this budget, starting with {wants_by_cat.index[0]}, would bring it in line."))
    if "Housing" in by_cat.index and by_cat["Housing"] / inc > 0.35:
        recs.append(("warn", f"Housing is {by_cat['Housing'] / inc:.0%} of income. Above about 30-35% it tends to squeeze the rest of your budget."))
    surplus = max(a["left"], 0)
    if savings_goal == "emergency":
        goal_text = (f"A starter emergency fund of 3 months of essentials is {money(3 * a['needs'])}. Consider directing part of your monthly surplus ({money(surplus)}) toward it." if a["needs"] > 0 else f"Add your essential expenses to estimate an emergency fund target. Your current monthly surplus is {money(surplus)}.")
        recs.insert(0, ("goal", goal_text))
    elif savings_goal == "debt":
        debt_payment = float(by_cat.get("Debt payments", 0))
        if debt_payment > 0:
            goal_text = f"You budget {money(debt_payment)} for debt payments. Keep minimums current and consider directing some of your {money(surplus)} monthly surplus to the highest-interest balance."
        else:
            goal_text = f"Add your monthly debt payments to see them in this report. Any available surplus ({money(surplus)}) could help pay down your highest-interest balance."
        recs.insert(0, ("goal", goal_text))
    elif savings_goal == "purchase":
        recs.insert(0, ("goal", f"Set a target amount and date for your purchase. You currently have {money(surplus)} left over each month that you could earmark toward it."))
    elif savings_goal == "investing":
        recs.insert(0, ("goal", f"After covering essentials, emergency savings, and high-interest debt, consider whether part of your {money(surplus)} monthly surplus fits your long-term investing goals."))
    if save_pct >= targets["Saved"] and a["pct"]["Wants"] <= targets["Wants"] and a["left"] >= 0:
        recs.insert(0, ("good", f"Nice work: savings and unspent income reach {save_pct:.0f}% and flexible spending is within your {targets['Wants']}% guideline."))
    return recs


def budget_insights(a, targets):
    positive, negative = [], []
    if a["left"] >= 0:
        positive.append(f"Income covers expenses, with {money(a['left'])} left over.")
    else:
        negative.append(f"Expenses exceed income by {money(-a['left'])}.")
    if a["pct"]["Needs"] <= targets["Needs"]:
        positive.append(f"Needs use {a['pct']['Needs']:.0f}% of income, within the {targets['Needs']}% guideline.")
    else:
        negative.append(f"Needs use {a['pct']['Needs']:.0f}% of income, above the {targets['Needs']}% guideline.")
    if a["pct"]["Wants"] <= targets["Wants"]:
        positive.append(f"Wants use {a['pct']['Wants']:.0f}% of income, within the {targets['Wants']}% guideline.")
    else:
        negative.append(f"Wants use {a['pct']['Wants']:.0f}% of income, above the {targets['Wants']}% guideline.")
    if a["pct"]["Saved"] >= targets["Saved"]:
        positive.append(f"Savings and unspent income reach {a['pct']['Saved']:.0f}%, meeting the {targets['Saved']}% guideline.")
    else:
        negative.append(f"Savings are {a['pct']['Saved']:.0f}% of income, below the {targets['Saved']}% guideline.")
    if not positive:
        positive.append("This report gives you a clear starting point for your budget.")
    if not negative:
        negative.append("No overspending or gaps from the selected guideline were found.")
    return positive, negative


# ---------- charts: responsive, native SVG ----------
# Distinct expense-category colors, separate from the app theme.
PALETTE = ["#2E8B57", "#3478C7", "#D1495B", "#E9822B", "#8E6CCF", "#159A9C", "#C49A18",
           "#BB5A9B", "#60A5D8", "#78A83B", "#E76F51", "#526D9A", "#A84A79", "#718096"]


def chart_empty():
    return '<div class="chart-empty"><span class="chart-empty-mark" aria-hidden="true">—</span><strong>No expense data yet</strong><span>Add an expense to see your spending breakdown.</span></div>'


def chart_donut(a):
    vals = list(a["by_cat"].items())
    if not vals or a["total"] <= 0:
        return chart_empty()
    total, circumference, offset = a["total"], 2 * np.pi * 42, 0
    arcs, legend = [], []
    for i, (name, value) in enumerate(vals):
        pct = value / total if total else 0
        dash = circumference * pct
        color = PALETTE[i % len(PALETTE)]
        arcs.append(f'<circle cx="60" cy="60" r="42" fill="none" stroke="{color}" stroke-width="13" data-category-index="{i}" style="--arc-length:{dash:.2f};--arc-gap:{circumference-dash:.2f};--arc-delay:{i * 55}ms" stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 60 60)"/>')
        offset += dash
        legend.append(f'<div class="chart-legend-row" data-category-index="{i}" tabindex="0" role="button" aria-label="Highlight {html.escape(str(name))} in the spending chart"><i style="background:{color}"></i><span>{html.escape(str(name))}</span><b>{money(value)}</b></div>')
    return '<div class="donut-layout"><svg class="donut" viewBox="0 0 120 120" role="img" aria-label="Spending distribution">' + ''.join(arcs) + f'<text x="60" y="57" text-anchor="middle" class="donut-total">{html.escape(money(a["total"]))}</text><text x="60" y="70" text-anchor="middle" class="donut-caption">total spent</text></svg><div class="chart-legend">' + ''.join(legend) + '</div></div>'


def chart_rule(a, targets):
    rows = []
    for key, target in targets.items():
        actual = a["pct"][key]
        color = COLORS[key]
        rows.append(f'<div class="compare-row"><div class="compare-label"><span>{key}</span><b>{actual:.0f}%</b></div><div class="track"><i style="width:{min(actual,100):.1f}%;background:{color}"></i><em style="left:{target}%" title="Guideline: {target}%"></em></div><div class="compare-foot"><span>You</span><span>Guideline {target}%</span></div></div>')
    return '<div class="compare-chart">' + ''.join(rows) + '</div>'


def chart_cats(a):
    s = (a["by_cat"] / a["income"] * 100).sort_values(ascending=False)
    if s.empty:
        return chart_empty()
    rows = []
    for i, (category, value) in enumerate(s.items()):
        color = PALETTE[i % len(PALETTE)]
        rows.append(f'<div class="category-row"><div class="category-label"><span>{html.escape(str(category))}</span><b>{value:.0f}%</b></div><div class="track"><i style="width:{min(value,100):.1f}%;background:{color}"></i></div></div>')
    return '<div class="category-chart">' + ''.join(rows) + '</div>'


# ---------- rendering ----------
def render():
    el("dashboard-step").hidden = step != "dashboard"
    for name in ("income", "expense", "preferences", "results"):
        el(f"{name}-step").hidden = step != name
    el("progress").hidden = step == "dashboard"
    if step == "dashboard":
        return
    for i in range(1, 5):
        el(f"p{i}").className = "active" if i == {"income": 1, "expense": 2, "preferences": 3, "results": 4}[step] else ""


def collect_expenses():
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
    return entries


def continue_to_preferences(e=None):
    global expenses, step
    try:
        parse_positive(el("income").value, "income")
        expenses = collect_expenses()
    except ValueError as ex:
        el("expense-err").textContent = str(ex)
        return
    el("expense-err").textContent = ""
    step = "preferences"
    render()


def show_results():
    global step
    try:
        income = parse_positive(el("income").value, "income")
    except ValueError as ex:
        el("income-err").textContent = str(ex)
        step = "income"
        render()
        return
    a = analyze(income, expenses)
    guideline_name, targets = current_guideline()
    profile, profile_note = spending_profile(a, targets)
    el("spending-profile").textContent = profile
    el("spending-profile-note").textContent = profile_note
    el("guideline-summary").textContent = guideline_name
    goal_names = {"": "General guidance", "emergency": "Build an emergency fund", "debt": "Pay down debt",
                  "purchase": "Save for a purchase", "investing": "Invest for the long term"}
    savings_goal = str(el("savings-goal").value)
    el("goal-summary").textContent = f"Focus: {goal_names.get(savings_goal, 'General guidance')}"
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
    positive, negative = budget_insights(a, targets)
    el("positives").innerHTML = "".join(f"<li>{html.escape(t)}</li>" for t in positive)
    el("negatives").innerHTML = "".join(f"<li>{html.escape(t)}</li>" for t in negative)
    el("recs").innerHTML = "".join(f"<li class='{k}'>{html.escape(t)}</li>" for k, t in recommendations(a, targets, savings_goal))
    el("c1").innerHTML, el("c2").innerHTML, el("c3").innerHTML = chart_donut(a), chart_rule(a, targets), chart_cats(a)
    el("c2").setAttribute("aria-label", f"Comparison to the {guideline_name} guideline")
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


def back_to_expenses(e):
    global step
    step = "expense"
    render()


def begin_budget(e=None):
    global step, expenses
    expenses = []
    el("income").value = ""
    el("income-err").textContent = ""
    el("expense-err").textContent = ""
    for i in range(len(GROUPS)):
        el(f"expense-{i}").value = ""
    el("guideline-model").value = "50/30/20"
    el("savings-goal").value = ""
    step = "income"
    render()


def back_to_preferences(e):
    global step
    step = "preferences"
    render()


def start_over(e):
    global step, expenses
    step, expenses = "dashboard", []
    el("income").value = ""
    el("income-err").textContent = ""
    el("expense-err").textContent = ""
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
    guideline_name, targets = current_guideline()
    writer.writerow(["Budget guideline", guideline_name])
    writer.writerow(["Total expenses", f'{a["total"]:.2f}'])
    writer.writerow(["Left over (negative means over budget)", f'{a["left"]:.2f}'])
    writer.writerow([])
    writer.writerow(["Budget group", "Spent", "Share of income", "Guideline"])
    for key, amount, target_key in (("Needs", a["needs"], "Needs"), ("Wants", a["wants"], "Wants"), ("Savings", a["saved_cat"], "Saved")):
        pct = amount / income * 100
        writer.writerow([key, f"{amount:.2f}", f"{pct:.1f}%", f'{targets[target_key]}%'])
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
    guideline_name, targets = current_guideline()
    return {"monthly": True, "period": "Monthly budget", "guideline_name": guideline_name, "guidelines": targets,
            "income": income, "total": a["total"], "left": a["left"], "needs": a["needs"],
            "wants": a["wants"], "saved": a["saved"], "saved_category": a["saved_cat"],
            "categories": [{"name": item["category"], "amount": item["amount"],
                            "group": GROUPS.get(item["category"], "Wants")} for item in expenses]}


def download_pdf(e=None):
    window.downloadBudgetPdf(json.dumps(pdf_report_data()))


def build_expense_fields():
    groups = {"Needs": [], "Wants": [], "Savings": []}
    descriptions = {"Needs": "Necessary Spending", "Wants": "Unnecessary Spending", "Savings": "Saving and Investing"}
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
el("back-expenses").addEventListener("click", create_proxy(back_to_expenses))
el("continue-preferences").addEventListener("click", create_proxy(continue_to_preferences))
el("show-results").addEventListener("click", create_proxy(lambda e: show_results()))
el("back-preferences").addEventListener("click", create_proxy(back_to_preferences))
el("download-csv").addEventListener("click", create_proxy(download_csv))
el("download-pdf").addEventListener("click", create_proxy(download_pdf))
el("start-over").addEventListener("click", create_proxy(start_over))
el("loading").hidden = True
render()
