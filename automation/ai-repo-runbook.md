# AI Repo Runbook

The runbook for this cron lives in the Google Doc **AI Repo Runbook**:
https://docs.google.com/document/d/1OqzJt01jwZLiZhNbK-_i6TLlqZrsrs-yTV2ZKJYJlak/edit

Its published copy is:
https://docs.google.com/document/d/e/2PACX-1vTxMEI1ruta5ZRahK8Kr_Dy_nni-y7XO6Mh7gzbMiEUgOphtABwHQEnTNXVSGp0OgZgTL9A-TxQY3kj/pub

Before each run that has work, the wrapper fetches the published copy as text into
`automation/runbook.fetched.md` (a generated file, not committed) and the run reads that.
Part 1 of the Doc applies to every repo; the section for this repo is under
"Repo-Specific Settings and Directives." To change the runbook, edit the Doc.

The cron code itself is not in this folder. Since 2026-09-07 there is one shared copy
in `C:\Users\Jeremy\Projects\repo-standard\automation` (repo `jeremynyc4/repo-standard`);
this repo's scheduled task runs the launcher there with this folder's `cron-config.json`
as its argument. That file holds the only per-repo values (project folder, repo, owner,
board number, launch flags). Everything else in this folder is runtime state, gitignored.
