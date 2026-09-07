# GWS Security Auditor (jeremynyc4 fork)

@.claude/instructions.md

The line above imports Jeremy's standing instructions (Coding Style Guide and
General AI Instructions) in a session that has no user-level CLAUDE.md, such as
a cloud sandbox. `.claude/fetch-instructions.py` writes that file at session
start; on Jeremy's laptop it writes a one-line stub because the user-level hook
already loads the same text. If the sandbox's network policy blocks the HTTP
fetch, the imported file instead asks you directly, as your first action, to
read the same two Docs through the Google Drive connector if one is attached
to this session. Follow that instruction if you see it.

A fork of [argusssec-cloud/gws-auditor](https://github.com/argusssec-cloud/gws-auditor)
(`upstream` remote), a Python tool that audits a Google Workspace tenant
against CIS, CISA SCuBA, and other security baselines. GitHub repo
`jeremynyc4/gws-auditor` (public, matching upstream), board TBD.

## Read first, every session

1. `README.md` — what the tool does, how it's invoked.
2. `CONTRIBUTING.md` — dev setup, project structure, how upstream expects
   changes to be made.
3. `docs/checks.md`, `docs/SETUP_GUIDE.md`, `docs/SOP_DOMAIN_SETUP.md` — the
   check catalog and the two setup walkthroughs (SOP is Jeremy's addition).

## This is a fork: two kinds of code live here

- **Upstream code** (`src/`, most of the repo) was written by the
  `argusssec-cloud` project, not under ABCD conventions. Match its existing
  style when touching it — PEP 8, the naming and structure already there.
  Rewriting it into ABCD's `fn`/typed-suffix conventions would make it
  unreadable against upstream diffs and impossible to contribute back
  cleanly; don't do that.
- **Jeremy's own additions** (anything new that isn't tracking an upstream
  file — automation/, docs he asked for, standalone scripts he owns) follow
  the full ABCD Coding Style Guide, same as every other repo.
- When genuinely unsure which a piece of code is, ask rather than guess.

## Handling real security data

This tool's whole purpose is auditing real Google Workspace tenants, so real
runs produce real, sensitive output.

- `domains/<domain>/` is each user's own local working directory (config,
  service-account credentials, cached API responses, audit reports) --
  gitignored wholesale. Never commit anything under it, and never assume a
  file found there is safe to read into a comment, a commit message, or
  anything else that leaves the machine.
- Two files were found sitting in the repo root outside any `domains/`
  folder and are **not** part of this project: personal exports of Jeremy's
  own Claude.ai account (conversations, memory, projects) and
  `header_probe.csv`, real Google Drive metadata from a probe run. Both are
  gitignored (2026-09-07) and must never be staged. If you see similar
  personal or credential-shaped files show up loose in the repo root, treat
  them the same way and flag it rather than touching them.
- Never print, log, or commit an OAuth key, a `config.yaml`, or a
  `dwd_scopes.txt` file's contents.

## Contributing upstream

Fixes that aren't specific to Jeremy's own use (bugs, general features) are
candidates for a PR to `argusssec-cloud/gws-auditor`, not just a commit here.
Ask before opening one -- this fork's own branches and Jeremy's judgment on
what upstream would want are what decide that, not a default action.
