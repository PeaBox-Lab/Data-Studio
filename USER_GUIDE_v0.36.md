# PBX Data Eng v0.36 User Guide

Open Ctrl+K and choose **Environment promotion**. Evaluate the gate before promotion. DEV to TEST allows non-blocking WARN findings, while promotion to PROD requires the latest CI/CD and contract test runs to have PASSED.

Use **Export deployment automation** to add `.github/workflows/pbx-environment-promotion.yml` to a repository. Configure TEST and PROD as GitHub Environments. For production, add required reviewers and any repository protection controls appropriate to your organization.
