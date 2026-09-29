# GitHub Profile Audit

Small, read-only GitHub API integration (REST with optional GraphQL) for checking public profile metadata and repository evidence.

It reports the profile record, public owner repositories, profile README presence/SHA, linked PR/issue state, and basic consistency checks. With `GITHUB_TOKEN`, it verifies a timestamped contribution snapshot against the exact UTC 365-day window ending at that timestamp. The snapshot's historical public-repository count is retained but cannot be re-verified; the current count is checked separately against the live profile. Date-only snapshots are left unverified because their rolling window is ambiguous. It never writes to GitHub or infers skill from stars, language percentages, or contribution counts.

Issue state and commenter GitHub associations are checked with the public REST API. If `GITHUB_TOKEN` is set, discussion metadata is checked with read-only GraphQL. The JSON report includes commenter identities/associations, never comment bodies. Without a token, discussion links are explicitly marked as requiring authenticated verification.

## Run

```bash
python3 github_profile_audit.py ducanhnguyen223 --out audit.json
python3 -m unittest -v
```

The public API is enough for small audits. Set `GITHUB_TOKEN` in the shell for
authenticated discussion checks or a higher API limit; the tool only uses it for
read-only requests and never writes it to the report.

The tool is a development integration for the [GitHub Developer Program](https://docs.github.com/en/integrations/concepts/github-developer-program). For support, contact [ducanhtq88@gmail.com](mailto:ducanhtq88@gmail.com).

Public API data is rate-limited and can change between requests. The JSON report is an observation, not a claim about skill or ownership beyond what GitHub returns.
