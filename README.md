# Budget Tracker

A browser-based budgeting tool. Enter your monthly income, add expenses by category and amount, and get a visual breakdown plus recommendations based on the 50/30/20 rule (50% needs, 30% wants, 20% savings). Built with Python (Pandas, NumPy, Matplotlib) running in the browser via PyScript, with a plain HTML/CSS interface.

## Run locally
1. Install Python 3.10+.
2. In this folder run: `python -m http.server 8000`
3. Open http://localhost:8000 (internet needed to load PyScript). The first load takes ~15 seconds.

The browser loads Pandas, NumPy, and Matplotlib itself (listed in `requirements.txt` and in the `config` of `index.html`), so no `pip install` is needed.

## Use online (GitHub Pages)
Push this folder to a GitHub repo, then Settings > Pages > Deploy from branch > `main` / root. Your link will be `https://<username>.github.io/<repo>/`.

## How to use
1. Type your monthly income after tax.
2. Pick a category, enter a monthly amount, and click "Add expense" (repeat). Remove mistakes with "Remove".
3. Results update automatically. "Try an example" fills in sample data.

## Files
- `index.html`: interface and styling
- `app.py`: Python logic (validation, Pandas analysis, recommendations, Matplotlib charts)
