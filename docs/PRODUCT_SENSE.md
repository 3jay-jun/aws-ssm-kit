# Product Sense

AWS Connect reduces repeated Windows AWS connection setup while preserving explicit
identity, MFA, target, and tunnel state.

- Optimize first for safe, understandable EC2 and saved RDS access.
- Show the active Account, IAM user, and token expiry before feature actions.
- Prefer recovery actions over raw SDK errors.
- Do not require AWS CLI or gossm in the distributed product.
- Do not hide target selection, overwrite decisions, or credential changes.
- Secrets Manager and S3 extend the same authenticated workspace after the MVP.

