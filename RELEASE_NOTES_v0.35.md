# PBX Data Eng v0.35 Release Notes

v0.35 extends CI/CD into release automation. A release gate combines code/contract CI, workspace health, data quality and lineage readiness. A generated GitHub workflow can validate tagged commits, package the release and create a GitHub Release only after the validation job succeeds.
