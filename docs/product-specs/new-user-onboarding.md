# New User Onboarding

## Desired outcome

A Windows user extracts the Portable ZIP, starts AWS Connect, stores a protected
AWS profile, completes MFA when required, and can diagnose prerequisites without
installing Python, AWS CLI, or gossm.

## MVP flow

1. Launch `aws_connect.exe`.
2. Create or select an AWS profile.
3. Validate identity and reuse or refresh the session token.
4. Use EC2 connection or a saved RDS tunnel session.
5. Use `aws_connect_cli.exe doctor` when local prerequisites fail.

Detailed acceptance criteria live in [the product requirements](aws-connect.md).

