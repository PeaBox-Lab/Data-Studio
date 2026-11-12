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

## The web workspace (bigger projects)

`data-studio serve` (after installing with `pip install -e .`) starts the full analytics
workbench in your browser. It works on *projects*: folders with your datasets as Parquet
plus the steps, analyses, model and dashboards you defined. Everything reopens exactly as
you left it — no pickle, and your original files are never touched.

Open it, then work from the sidebar: **Data** (import, clean, join, filter), **Prepare**
(pipeline steps with undo), **Analyze** (17 typed analyses), **Dashboards** and **Saved**.

Dashboards worth knowing about:

- **Calendar & time intelligence.** Generate a calendar in the Model panel
  ("Generate calendar"), or `data-studio date-table . --start 2020-01-01 --end 2030-12-31`.
  Then ask for `running_total(sum(amount))` or `yoy_pct(sum(amount))` in a measure — ordered
  by the date dimension you mark with a grain. The model panel has a grain dropdown per date
  dimension.
- **Drill-down & cross-highlighting.** Click a chart bar or a table row to filter every
  tile; breadcrumbs above a hierarchy chart walk year → quarter → month → day.
- **What-if parameters.** Declare them on the dashboard ("Add parameter"), reference them
  as `@name` in a tile filter or Top N, then change them live in the parameter bar.
- **Refresh on schedule.** Each dashboard can be `manual`, `on open`, `hourly` or `daily`;
  the workspace refreshes due dashboards while it's open, and `data-studio refresh --due`
  from Task Scheduler covers unattended runs. A dashboard shows a **Fresh/Stale** pill.

Terminal equivalents live under `data-studio` (see `README.md`): `model-derive`,
`model-set`, `date-table`, `dashboard-save`, `dashboard-render`, `dashboard-report`,
`script`, `pivot` (via the API), `refresh --due`.

Handy new spots in the workspace:

- **Prepare → Formula bar** for typed expressions (`round(amount * 1.2, 2)`) with live checks, plus fill down, find &amp; replace, rename, cast, drop and copy-as-TSV.
- **Pivot** (new screen) for quick cross-tabs with per-value aggregations — including a one-click "Add to pipeline".
- **Data → Preview → Conditional formatting** to colour cells once and forget: data bars, heatmap or highlight rules saved in the project. The desktop app adds **Lists…** to restrict a column to allowed values.
