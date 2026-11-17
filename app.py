"""Budget Tracker: input (income + expenses) -> processing (Pandas) -> output (summary, Matplotlib charts, recommendations)."""
import html
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


def recommendations(a):
    inc, recs = a["income"], []
    by_cat = a["by_cat"]
    wants_by_cat = by_cat[[c for c in by_cat.index if GROUPS.get(c, "Wants") == "Wants"]]
    if a["left"] < 0:
        recs.append(("warn", f"You are spending {money(-a['left'])} more than you earn each month. Start with your largest 'wants' category"
                     + (f", {wants_by_cat.index[0]} ({money(wants_by_cat.iloc[0])})." if len(wants_by_cat) else ".")))
    save_pct = a["pct"]["Saved"]
    if a["left"] >= 0 and save_pct < 20:
        recs.append(("tip", f"You save {save_pct:.0f}% of your income. Saving 20% would mean putting aside {money(inc * 0.2 - a['saved'])} more per month."))
    if a["pct"]["Needs"] > 50:
        recs.append(("warn" if a["pct"]["Needs"] > 60 else "tip", f"Essentials take {a['pct']['Needs']:.0f}% of income (guideline: 50%). Review your biggest need, {by_cat[[c for c in by_cat.index if GROUPS.get(c) == 'Needs']].index[0]}, for savings such as a cheaper plan or provider."))
    if a["pct"]["Wants"] > 30 and len(wants_by_cat):
        cut = a["wants"] - inc * 0.3
        recs.append(("tip", f"Wants are {a['pct']['Wants']:.0f}% of income (guideline: 30%). Trimming {money(cut)}, starting with {wants_by_cat.index[0]}, would bring you in line."))
    if "Housing" in by_cat.index and by_cat["Housing"] / inc > 0.35:
        recs.append(("warn", f"Housing is {by_cat['Housing'] / inc:.0%} of income. Above about 30-35% it tends to squeeze the rest of your budget."))
    needs_total = a["needs"]
    if a["saved"] > 0 and needs_total > 0:
        months = 3 * needs_total / a["saved"]
        recs.append(("tip", f"An emergency fund of 3 months of essentials is {money(3 * needs_total)}. At your current saving rate you would reach it in about {months:.0f} months."))
    if save_pct >= 20 and a["pct"]["Wants"] <= 30 and a["left"] >= 0:
        recs.insert(0, ("good", f"Nice work: you are saving {save_pct:.0f}% of your income and keeping wants at {a['pct']['Wants']:.0f}%."))
    return recs


# ---------- charts: responsive, native SVG ----------
PALETTE = ["#6D8196", "#8A9A83", "#A88D70", "#4A4A4A", "#B5A88D", "#8799A8", "#CBCBCB"]


def chart_donut(a):
    vals = list(a["by_cat"].items())
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
    rows = []
    for category, value in s.items():
        color = COLORS["Saved"] if GROUPS.get(category) == "Savings" else COLORS["Needs"] if GROUPS.get(category) == "Needs" else COLORS["Wants"]
        rows.append(f'<div class="category-row"><div class="category-label"><span>{html.escape(str(category))}</span><b>{value:.0f}%</b></div><div class="track"><i style="width:{min(value,100):.1f}%;background:{color}"></i></div></div>')
    return '<div class="category-chart">' + ''.join(rows) + '</div>'


# ---------- rendering ----------
def render():
    il = el("items")
    il.innerHTML = ""
    for i, e in enumerate(expenses):
        li = document.createElement("li")
        li.innerHTML = f"<span>{html.escape(e['category'])}</span><span>{money(e['amount'])} "
        btn = document.createElement("button")
        btn.className, btn.textContent = "link", "Remove"
        btn.setAttribute("aria-label", f"Remove {e['category']} {money(e['amount'])}")
        btn.addEventListener("click", create_proxy(lambda ev, i=i: remove(i)))
        li.lastChild.appendChild(btn)
        il.appendChild(li)
    el("none").hidden = bool(expenses)

    try:
        income = parse_positive(el("income").value, "income")
    except ValueError:
        income = None
    if income is None or not expenses:
        el("results").hidden = True
        el("prompt").parentElement.hidden = False
        return
    el("prompt").parentElement.hidden = True
    el("results").hidden = False

    a = analyze(income, expenses)
    big = el("big")
    if a["left"] >= 0:
        big.className, big.textContent = "big", f"{money(a['left'])} left"
        el("bigsub").textContent = f"of {money(income)} after {money(a['total'])} in expenses ({a['saved'] / income:.0%} saved or unspent)."
    else:
        big.className, big.textContent = "big bad", f"{money(-a['left'])} over"
        el("bigsub").textContent = f"Expenses of {money(a['total'])} exceed your income of {money(income)}."
    scale = max(income, a["total"])
    parts = [("Needs", a["needs"], COLORS["Needs"]), ("Wants", a["wants"], COLORS["Wants"]),
             ("Savings", a["saved_cat"], COLORS["Saved"]), ("Unspent", max(a["left"], 0), "#c5cbd8")]
    el("split").innerHTML = "".join(f"<div style='width:{v / scale * 100:.2f}%;background:{c}'></div>" for _, v, c in parts)
    el("legend").innerHTML = "".join(f"<span><i class='dot' style='background:{c}'></i>{n} {money(v)}</span>" for n, v, c in parts)
    el("recs").innerHTML = "".join(f"<li class='{k}'>{html.escape(t)}</li>" for k, t in recommendations(a))
    el("c1").innerHTML, el("c2").innerHTML, el("c3").innerHTML = chart_donut(a), chart_rule(a), chart_cats(a)


# ---------- events ----------
def err(msg=""):
    el("err").textContent = msg


def on_submit(e):
    e.preventDefault()
    try:
        parse_positive(el("income").value, "income")
        amt = parse_positive(el("amt").value, "amount")
    except ValueError as ex:
        return err(str(ex))
    err()
    expenses.append({"category": el("cat").value, "amount": amt})
    el("amt").value = ""
    render()


def remove(i):
    expenses.pop(i)
    render()


def on_example(e):
    global expenses
    el("income").value = "3200"
    expenses = [{"category": c, "amount": v} for c, v in
                [("Housing", 1100), ("Groceries", 380), ("Transport", 150), ("Utilities", 120), ("Dining out", 290),
                 ("Entertainment", 140), ("Subscriptions", 45), ("Shopping", 210), ("Savings & investing", 150)]]
    err()
    render()


def on_clear(e):
    expenses.clear()
    el("income").value = el("amt").value = ""
    err()
    render()


el("cat").innerHTML = "".join(f"<option>{html.escape(c)}</option>" for c in GROUPS)
el("form").addEventListener("submit", create_proxy(on_submit))
el("income").addEventListener("input", create_proxy(lambda e: render()))
el("example").addEventListener("click", create_proxy(on_example))
el("clear").addEventListener("click", create_proxy(on_clear))
el("loading").hidden = True
render()
