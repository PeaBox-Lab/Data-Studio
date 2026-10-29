# PBX Data Eng v0.31 Release Notes

v0.31 extends governance from contracts to lineage. The workbench can inventory project assets and trace known downstream dependencies from the active dataset, helping users identify pipelines, dashboards, models and APIs that may require validation after a breaking data change.

Recommended workflow: review v0.30 contract conformance, run v0.31 impact analysis before schema changes, apply changes in a controlled pipeline, then retest downstream assets.
