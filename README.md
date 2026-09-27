# GitHub Profile Audit

Small, read-only GitHub REST API integration for checking public profile metadata and repository evidence.

It reports the profile record, public owner repositories, profile README presence/SHA, and basic consistency checks. It does not write to GitHub, require a token, or infer skill from stars, language percentages, or contribution counts.

## Run

```bash
python3 github_profile_audit.py ducanhnguyen223 --out audit.json
python3 -m unittest -v
```

The tool is a development integration for the [GitHub Developer Program](https://docs.github.com/en/integrations/concepts/github-developer-program). For support, contact [ducanhtq88@gmail.com](mailto:ducanhtq88@gmail.com).

Public API data is rate-limited and can change between requests. The JSON report is an observation, not a claim about skill or ownership beyond what GitHub returns.
