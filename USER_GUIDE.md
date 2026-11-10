# Data Studio - quick guide

Data Studio is a small, friendly desktop app for everyday data work.
Open a file, tidy it, chart it, save it. That is all.

## Install and start

Double-click `run_data_studio.bat`, or from a terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m data_studio.ui
```

## The five screens

| Screen | What it does |
|---|---|
| Home | Open your file (CSV / Excel / JSON / Parquet), or try the built-in sample |
| My table | See and search your data. Click any cell to fix it. Amber = missing |
| Clean | One-click fixes: fill gaps, drop duplicates, trim spaces, text-to-numbers |
| Charts | Bar, line, scatter or histogram from any two columns. Save as PNG |
| Save & share | Save CSV / Excel / Parquet copies, or copy a plain-English summary |

## Good to know

- **Your original file is never overwritten.** You work on a copy in the app.
- **Ctrl+Z** undoes the last fix, as far back as you like.
- **It opened the wrong thing / failed?** A message tells you exactly why -
  usually an unsupported or password-protected file.
- Hover any button if you are unsure what it does.

Want the classic all-features workbench? It lives in [`legacy/`](legacy/README.md).
