# Budget Tracker

A browser-based budgeting tool. Choose monthly tracking or a longer-term report, enter income and expenses, and get a visual breakdown plus recommendations based on the 50/30/20 rule. Monthly budgets can be compared with other saved months. Download a CSV, a PDF report with charts, or both.

The browser loads Pandas and NumPy through PyScript, so no `pip install` is needed. Monthly reports are saved in the browser on the current device.

## Link to Access
- https://sasc-5104.github.io/budget_app/

## How to use
1. Choose month-by-month tracking or a longer-term report.
2. Enter income and applicable category expenses for the same period.
3. View the report and choose CSV, PDF, or both to download.

## Files
- `index.html`: interface and styling
- `app.py`: Python logic, validation, analysis, recommendations, and CSV export
- `pdf_export.js`: client-side PDF report and vector charts
