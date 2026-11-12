# Legacy (v0.38 desktop prototype)

This folder is the **frozen** full-featured Data Studio desktop prototype.
It is kept runnable as a reference and for anyone who still needs the
all-features workbench (governance, rollback, observability, jobs, AutoML…).

It is intentionally **not** developed further. New work happens in
`data_studio/ui/` (the simple desktop app) and `data_studio/` (the package).

## Run it

```powershell
cd legacy
pip install -r requirements_v0.38.txt
python data_studio_v0.38.py
```

The flat modules it imports (`data_studio_core.py` etc., kept at the repo root
without version numbers) are shared with the test suite. Docs from the v0.38
era sit alongside the code here for reference.
