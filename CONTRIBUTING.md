# Contributing to JevLoop

All changes to `main` must go through a pull request, including changes made by
the repository owner, administrators, and automation.

## Development workflow

1. Start from the latest `main` and create a focused branch. Suggested names are
   `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, or `chore/<topic>`; these prefixes
   are conventions, not enforced naming restrictions.
2. Keep the change focused and preserve unrelated work. External contributors
   should push to a fork; maintainers may push a branch to this repository.
3. Open a pull request targeting `main`. Explain the problem, resulting behavior,
   validation performed, and relevant limitations. Link related issues when useful.
4. Address review feedback and resolve review conversations before merging.
5. Use **Squash and merge**. The PR title becomes the commit title; use a concise
   description of the final change. Repository branches are deleted automatically
   after merge; contributors manage branches in their own forks.

Example:

```bash
git switch main
git pull --ff-only
git switch -c feat/my-change
# Make and commit your change.
git push -u origin feat/my-change
gh pr create --base main
```

## Enforced main-branch rules

The active [main-pr-only ruleset](https://github.com/parkavenue9639/jevloop/rules/23808793)
requires PRs, resolved review conversations, and linear history. Direct pushes,
force pushes, and deletion of `main` are blocked. The bypass list is empty:
there is no owner, administrator, or automation exemption.

This is currently a single-maintainer project, so **zero approving reviews are
required**. A maintainer may merge their own PR; a PR is still mandatory. There
are currently no required CI status checks. Record the checks actually performed
and any checks not run instead of implying validation that did not happen.

The rules apply to code, documentation, dependencies, releases, and repository
workflow files alike. Urgent fixes follow the same PR path. Do not disable the
rules or introduce a bypass as part of routine development.

## Scope and issue labels

Use the existing issue types such as `bug`, `enhancement`, or `documentation`,
and add an area label where relevant:

- `area:runtime`: decisions, drivers, execution, and guardrails.
- `area:dashboard`: web UI, history, comparison, and replay.
- `area:integrations`: tools, models, and platform adapters.
- `area:evaluation`: paired comparisons, measurements, and evidence.

Keep credentials, private account data, and raw local run artifacts out of PRs.
Public evidence should be reviewed for sensitive content before submission.
