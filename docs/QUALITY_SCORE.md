# Quality Score

Update this table at phase gates using command evidence from the active plan.

| Area | Target | Current | Evidence |
|---|---:|---:|---|
| Documentation map | 100% | 100% | `scripts/verify-docs.ps1`, 2026-09-14 pass |
| Static and architecture checks | 100% | 100% | Ruff, mypy, architecture/import gates, 2026-09-14 pass |
| Application / Domain line coverage | >= 90% | 94.40% / 95.56% | 302-test pytest-cov XML, 2026-09-14 |
| Overall line coverage | >= 80% | 85.83% | 302 passed, 2026-09-14 |
| Critical branch coverage | 100% | 100% | 5-file XML gate: auth/MFA, masking, CLI/GUI errors, 2026-09-14 |
| Sensitive-data regressions | 0 | 0 | detect-secrets and Bandit, 2026-09-14 pass |
| Dependency vulnerabilities | 0 | 0 | `pip-audit`, 2026-09-14 (`aws-connect` itself is local-only) |
| Windows runtime ACL | Pass | Pass | real journal/WAL/SHM and log-rollover SDDL tests, 2026-09-14 |
| Package smoke | Pass | Rebuild required | prior TEST-ONLY smoke passed; source changed afterward |
