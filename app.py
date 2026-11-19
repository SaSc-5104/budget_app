"""Budget Tracker: browser-side budget analysis with responsive SVG charts."""
import html
import csv
import io
import json
from datetime import datetime
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
saved_months = {}
month_proxies = []
monthly_mode = False


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
        render_dashboard()
        return
    for i in range(1, 4):
        el(f"p{i}").className = "active" if i == {"income": 1, "expense": 2, "results": 3}[step] else ""
    el("month-chooser").hidden = not monthly_mode
    el("comparison-card").hidden = not monthly_mode


def show_results():
    global expenses, step, saved_months
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
    month = el("budget-month").value if monthly_mode else ""
    if monthly_mode:
        if len(month) != 7 or month[4] != "-":
            el("expense-err").textContent = "Choose a month to continue."
            return
        month_record = {"income": income, "total": a["total"], "left": a["left"],
                        "needs": a["needs"], "wants": a["wants"], "saved": a["saved"],
                        "categories": {item["category"]: item["amount"] for item in expenses}}
        saved_months[month] = month_record
        try:
            window.localStorage.setItem("budget-months", json.dumps(saved_months))
        except Exception:
            pass
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
    el("recs").innerHTML = "".join(f"<li class='{k}'>{html.escape(t)}</li>" for k, t in recommendations(a, monthly_mode))
    el("c1").innerHTML, el("c2").innerHTML, el("c3").innerHTML = chart_donut(a), chart_rule(a), chart_cats(a)
    if monthly_mode:
        render_comparison(month)
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
    if monthly_mode and not el("budget-month").value:
        el("income-err").textContent = "Choose a month and year."
        return
    el("income-err").textContent = ""
    step = "expense"
    render()


def set_report_mode(is_monthly):
    global monthly_mode
    monthly_mode = is_monthly
    el("mode-monthly").className = "mode-option selected" if monthly_mode else "mode-option"
    el("mode-longterm").className = "mode-option" if monthly_mode else "mode-option selected"
    el("mode-monthly").setAttribute("aria-pressed", "true" if monthly_mode else "false")
    el("mode-longterm").setAttribute("aria-pressed", "false" if monthly_mode else "true")
    hint = el("expense-hint")
    if hint is not None:
        hint.textContent = "Monthly amounts. Leave any categories that do not apply blank." if monthly_mode else "Enter amounts for the period covered by this report. Leave others blank."
    if monthly_mode:
        load_month_form(el("budget-month").value)
    if step != "dashboard":
        render()


def back_to_income(e):
    global step
    step = "income"
    render()


def begin_budget(e=None):
    global step
    set_report_mode(False)
    el("income").value = ""
    el("income-err").textContent = ""
    el("expense-err").textContent = ""
    for i in range(len(GROUPS)):
        el(f"expense-{i}").value = ""
    step = "income"
    render()


def begin_month_budget(month):
    global step
    set_report_mode(True)
    el("income").value = ""
    el("income-err").textContent = ""
    el("expense-err").textContent = ""
    for i in range(len(GROUPS)):
        el(f"expense-{i}").value = ""
    el("budget-month").value = month
    build_month_choices(month)
    step = "income"
    render()


def go_dashboard(e=None):
    global step
    step = "dashboard"
    render()


def view_saved_month(month):
    global step
    set_report_mode(True)
    el("budget-month").value = month
    load_month_form(month)
    build_month_choices(month)
    step = "expense"
    render()
    show_results()


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
    writer.writerow(["Report type", "Month by month" if monthly_mode else "Longer-term report"])
    if monthly_mode:
        writer.writerow(["Month", month_label(el("budget-month").value)])
    writer.writerow(["Income for period", f'{income:.2f}'])
    writer.writerow(["Total expenses", f'{a["total"]:.2f}'])
    writer.writerow(["Left over (negative means over budget)", f'{a["left"]:.2f}'])
    writer.writerow([])
    writer.writerow(["Budget group", "Spent", "Share of income", "Guideline"])
    for key, amount, target_key in (("Needs", a["needs"], "Needs"), ("Wants", a["wants"], "Wants"), ("Savings", a["saved_cat"], "Saved")):
        pct = amount / income * 100
        writer.writerow([key, f"{amount:.2f}", f"{pct:.1f}%", f'{TARGET[target_key]}%'])
    writer.writerow([])
    writer.writerow(["Category", "Group", "Amount for period", "Share of income"])
    for item in expenses:
        writer.writerow([item["category"], GROUPS.get(item["category"], "Wants"), f'{item["amount"]:.2f}', f'{item["amount"] / income * 100:.1f}%'])
    anchor = document.createElement("a")
    anchor.href = "data:text/csv;charset=utf-8," + quote(output.getvalue())
    anchor.download = f"budget-{el('budget-month').value}.csv" if monthly_mode else "budget-summary.csv"
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()


def pdf_report_data():
    income = parse_positive(el("income").value, "income")
    a = analyze(income, expenses)
    month = el("budget-month").value if monthly_mode else ""
    return {"monthly": monthly_mode, "period": month_label(month) if month else "Longer-term report",
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


def month_label(key):
    return datetime.strptime(key, "%Y-%m").strftime("%B %Y")


def short_month_label(key):
    return datetime.strptime(key, "%Y-%m").strftime("%b %Y")


def render_comparison(current_month):
    options = sorted((key for key in saved_months if key != current_month), reverse=True)
    select = el("compare-month")
    select.innerHTML = '<option value="">Choose a month</option>' + "".join(
        f'<option value="{key}">{month_label(key)}</option>' for key in options)
    el("comparison-note").hidden = bool(options)
    el("comparison-content").hidden = not bool(options)
    if options:
        select.value = options[0]
        render_comparison_detail(current_month, options[0])
    else:
        el("comparison-table").innerHTML = ""


def render_comparison_detail(current_month, compare_month):
    if not compare_month or compare_month not in saved_months:
        el("comparison-content").hidden = True
        el("comparison-note").hidden = False
        el("comparison-table").innerHTML = ""
        return
    el("comparison-content").hidden = False
    el("comparison-note").hidden = True
    current, previous = saved_months[current_month], saved_months[compare_month]
    metrics = [("Income", "income"), ("Spent", "total"), ("Left over", "left")]
    cards = []
    for label, key in metrics:
        now, before = current[key], previous[key]
        delta = now - before
        change = "No change" if delta == 0 else f'{"Up" if delta > 0 else "Down"} {money(abs(delta))}'
        change_class = "flat" if delta == 0 else "up" if delta > 0 else "down"
        cards.append(f'<article class="compare-stat"><span class="compare-stat-title">{label}</span><div class="compare-stat-pair"><div><small>{month_label(current_month)}</small><b>{money(now)}</b></div><div><small>{month_label(compare_month)}</small><b>{money(before)}</b></div></div><span class="compare-delta {change_class}">{change}</span></article>')
    categories = set(current["categories"]) | set(previous["categories"])
    changed = sorted((c for c in categories if current["categories"].get(c, 0) != previous["categories"].get(c, 0)),
                     key=lambda c: abs(current["categories"].get(c, 0) - previous["categories"].get(c, 0)), reverse=True)
    cat_rows = []
    for category in changed[:5]:
        now, before = current["categories"].get(category, 0), previous["categories"].get(category, 0)
        delta = now - before
        change = f'{"Up" if delta > 0 else "Down"} {money(abs(delta))}'
        change_class = "up" if delta > 0 else "down"
        scale = max(now, before, 1)
        current_width, previous_width = now / scale * 100, before / scale * 100
        cat_rows.append(f'<article class="compare-category"><div class="compare-category-head"><b>{html.escape(category)}</b><span class="compare-delta {change_class}">{change}</span></div><div class="compare-bar-line"><small>{short_month_label(current_month)}</small><div class="compare-track"><i style="width:{current_width:.1f}%"></i></div><b>{money(now)}</b></div><div class="compare-bar-line"><small>{short_month_label(compare_month)}</small><div class="compare-track previous"><i style="width:{previous_width:.1f}%"></i></div><b>{money(before)}</b></div></article>')
    category_content = "".join(cat_rows) if cat_rows else '<p class="empty">No category spending changed between these months.</p>'
    detail = f'<p class="comparison-caption">{month_label(current_month)} compared with {month_label(compare_month)}</p><div class="compare-summary">{"".join(cards)}</div><h3 class="compare-section-title">Biggest category changes</h3><div class="compare-categories">{category_content}</div>'
    if len(changed) > 5:
        detail += f'<p class="comparison-footnote">Showing the 5 largest changes across {len(changed)} categories.</p>'
    el("comparison-table").innerHTML = detail


def on_compare_change(e):
    current_month = el("budget-month").value
    render_comparison_detail(current_month, el("compare-month").value)


def load_month_form(month):
    record = saved_months.get(month)
    if record:
        el("income").value = str(record["income"])
        categories = record.get("categories", {})
        for i, category in enumerate(GROUPS):
            amount = categories.get(category, "")
            el(f"expense-{i}").value = str(amount) if amount else ""
    else:
        for i in range(len(GROUPS)):
            el(f"expense-{i}").value = ""


def choose_budget_month(month):
    if month == el("budget-month").value:
        return
    el("budget-month").value = month
    load_month_form(month)
    for month_number in range(1, 13):
        key = f"{month[:4]}-{month_number:02d}"
        button = el(f"month-{key}")
        selected = key == month
        button.className = "month-pill selected" if selected else "month-pill"
        button.setAttribute("aria-pressed", "true" if selected else "false")


def available_budget_years():
    current_year = datetime.now().year
    years = set(range(current_year - 10, current_year + 1))
    years.update(int(key[:4]) for key in saved_months)
    return sorted(years, reverse=True)


def build_month_choices(selected):
    for proxy in month_proxies:
        proxy.destroy()
    month_proxies.clear()
    years = available_budget_years()
    selected_year = int(selected[:4]) if selected else (int(el("budget-year").value) if el("budget-year").value else datetime.now().year)
    if selected_year not in years:
        years.append(selected_year)
        years.sort(reverse=True)
    year_select = el("budget-year")
    year_select.innerHTML = "".join(f'<option value="{year}">{year}</option>' for year in years)
    year_select.value = str(selected_year)
    container = el("month-pills")
    container.innerHTML = ""
    for month_number in range(1, 13):
        month = f"{selected_year:04d}-{month_number:02d}"
        date = datetime.strptime(month, "%Y-%m")
        button = document.createElement("button")
        button.type = "button"
        button.className = "month-pill selected" if month == selected else "month-pill"
        button.setAttribute("aria-pressed", "true" if month == selected else "false")
        button.setAttribute("aria-label", month_label(month))
        button.title = month_label(month)
        button.id = f"month-{month}"
        button.innerHTML = f"<span>{date.strftime('%b')}</span>"
        proxy = create_proxy(lambda event, key=month: choose_budget_month(key))
        month_proxies.append(proxy)
        button.addEventListener("click", proxy)
        container.appendChild(button)


def on_budget_year_change(e):
    el("budget-month").value = ""
    for i in range(len(GROUPS)):
        el(f"expense-{i}").value = ""
    build_month_choices("")


def render_dashboard():
    months = sorted(saved_months, reverse=True)
    stats = el("dashboard-stats")
    if not months:
        stats.hidden = True
        stats.innerHTML = ""
    else:
        latest = saved_months[months[0]]
        balance_label = "Left over" if latest["left"] >= 0 else "Over budget"
        balance = money(abs(latest["left"]))
        stats.hidden = False
        stats.innerHTML = f'<div class="dashboard-stat"><small>Months saved</small><b>{len(months)}</b></div><div class="dashboard-stat"><small>Latest budget</small><b>{month_label(months[0])}</b></div><div class="dashboard-stat"><small>{balance_label}</small><b>{balance}</b></div>'
    years = sorted({datetime.now().year} | {int(key[:4]) for key in months}, reverse=True)
    year_select = el("dashboard-calendar-year")
    selected_year = int(year_select.value) if year_select.value and int(year_select.value) in years else years[0]
    year_select.innerHTML = "".join(f'<option value="{year}">{year}</option>' for year in years)
    year_select.value = str(selected_year)
    calendars = []
    for year in (selected_year,):
        tiles = []
        for month_number in range(1, 13):
            key = f"{year:04d}-{month_number:02d}"
            month_name = datetime(year, month_number, 1).strftime("%b")
            record = saved_months.get(key)
            if record:
                if record["left"] > 0:
                    balance = f'{money(record["left"])} left'
                elif record["left"] < 0:
                    balance = f'{money(-record["left"])} over'
                else:
                    balance = "Balanced"
                tiles.append(f'<div class="calendar-cell"><button type="button" class="month-tile saved" data-action="view" data-month="{key}" aria-label="View {month_name} {year} report"><strong>{month_name}</strong><small>Saved</small><b>{balance}</b></button><button type="button" class="calendar-delete" data-action="delete" data-month="{key}" aria-label="Delete {month_name} {year} budget">×</button></div>')
            else:
                tiles.append(f'<button type="button" class="month-tile empty" data-action="start" data-month="{key}" aria-label="Start a budget for {month_name} {year}"><strong>{month_name}</strong><small>Not saved</small><b>+ Add budget</b></button>')
        calendars.append(f'<section class="calendar-year"><h3>{year}</h3><div class="month-calendar">{"".join(tiles)}</div></section>')
    el("saved-budgets").innerHTML = "".join(calendars)


def delete_saved_month(month):
    global saved_months
    if month not in saved_months:
        return
    saved_months.pop(month)
    try:
        window.localStorage.setItem("budget-months", json.dumps(saved_months))
    except Exception:
        pass
    if el("budget-month").value == month:
        el("budget-month").value = datetime.now().strftime("%Y-%m")
        el("income").value = ""
        for i in range(len(GROUPS)):
            el(f"expense-{i}").value = ""
    build_month_choices(el("budget-month").value)
    render_dashboard()


def on_dashboard_action(e):
    target = e.target.closest("button[data-action]")
    if target is None:
        return
    action = target.getAttribute("data-action")
    month = target.getAttribute("data-month")
    if action == "delete":
        delete_saved_month(month)
    elif action == "view":
        view_saved_month(month)
    elif action == "start":
        begin_month_budget(month)


build_expense_fields()
el("budget-month").value = datetime.now().strftime("%Y-%m")
try:
    raw_months = window.localStorage.getItem("budget-months")
    if raw_months:
        saved_months = json.loads(str(raw_months))
except Exception:
    saved_months = {}
if monthly_mode:
    load_month_form(el("budget-month").value)
build_month_choices(el("budget-month").value)
el("income-form").addEventListener("submit", create_proxy(on_income_submit))
el("start-budget").addEventListener("click", create_proxy(begin_budget))
el("income-dashboard").addEventListener("click", create_proxy(go_dashboard))
el("go-dashboard").addEventListener("click", create_proxy(go_dashboard))
el("mode-monthly").addEventListener("click", create_proxy(lambda e: set_report_mode(True)))
el("mode-longterm").addEventListener("click", create_proxy(lambda e: set_report_mode(False)))
el("budget-year").addEventListener("change", create_proxy(on_budget_year_change))
el("dashboard-calendar-year").addEventListener("change", create_proxy(lambda e: render_dashboard()))
el("back-income").addEventListener("click", create_proxy(back_to_income))
el("show-results").addEventListener("click", create_proxy(lambda e: show_results()))
el("edit-expenses").addEventListener("click", create_proxy(edit_expenses))
el("download-csv").addEventListener("click", create_proxy(download_csv))
el("download-pdf").addEventListener("click", create_proxy(download_pdf))
el("compare-month").addEventListener("change", create_proxy(on_compare_change))
el("start-over").addEventListener("click", create_proxy(start_over))
el("loading").hidden = True
dashboard_action_proxy = create_proxy(on_dashboard_action)
el("saved-budgets").addEventListener("click", dashboard_action_proxy)
render()
