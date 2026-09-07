#!/usr/bin/env python3
# >>>>> detect-work.py <<<<<
# Cheap, read-only detector for the Claude cron. Runs entirely on the GitHub CLI
# (no model calls), so it is nearly free to run every few minutes. It answers one
# question: is there anything for the unattended Claude to act on right now?
#
# Two kinds of work, matching Jeremy's spec:
#   1. Board items in "Do Now" status. Gated purely on the board Status field. The
#      issue's Open/Closed flag is deliberately ignored -- Status is the single source
#      of truth for this board, and consulting two state systems is exactly what lets
#      them drift out of agreement.
#
#      ONE ITEM AT A TIME. Only the first Do Now item in board order is handed to a run;
#      the rest stay in Do Now untouched. The run moves on to the next one itself when
#      its item is Submitted or Paused, and the wrapper re-detects after every run.
#      Jeremy's rule of 2026-09-05, after a run given seven items at once paused all
#      seven rather than work on any of them. The count left waiting is reported so
#      the log can show the queue depth.
#   2. Comments containing "@claude" or "@claudebot-ymerej" that are newer than the
#      most recent comment Claude itself left on that issue. Found through one REST
#      listing of the repo's comments since a persisted watermark, regardless of
#      Open/Closed, so a pass costs about one REST point per page rather than about a
#      hundred GraphQL points. The watermark (mention-watermark.json, gitignored) is
#      the newest comment timestamp seen by the last successful run, so a gap of any
#      length -- five minutes or five weeks away -- is covered on the next run rather
#      than only whatever a fixed lookback window covers. Only the first run ever, with
#      no watermark on disk yet, falls back to cLookbackDays_int.
#
#      Claude now posts as its own account, "claudebot-ymerej", so its comments are
#      identified by author. Historically it posted as jeremynyc4 -- the same account
#      as Jeremy -- which made author useless, so every automated reply carried a
#      hidden marker instead. Both signals are honoured: a comment counts as Claude's
#      if the author is the bot OR the body carries the marker. The marker path exists
#      purely for comments written before the bot account, and can be dropped once
#      those are old enough not to matter.
#
# Output: JSON on stdout describing the work. Exit code is always 0 unless the
# GitHub calls themselves fail, so the wrapper can tell "no work" (cheap, skip
# Claude) apart from "detection broke" (skip Claude, log the error).

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cRepo_str      = "jeremynyc4/gws-auditor"
cOwner_str     = "jeremynyc4"
cProject_int   = 5
cDoNowStatus   = "Do Now"
# Either handle summons the cron. Note "@claude" is a substring of "@claudebot-ymerej",
# so the bot handle already matched before it was listed -- both are spelled out anyway so
# the behaviour is intentional and survives someone renaming the bot.
cMentions_arr  = ("@claude", "@claudebot-ymerej")
cMarker_str    = "<!-- claude-cron:handled -->"
cBotLogin_str  = "claudebot-ymerej"
# Where the mention-scan watermark is kept. Gitignored: it is per-machine run state, not
# project history.
cWatermarkFile_pth = Path(__file__).resolve().parent / "mention-watermark.json"
# Bootstrap window used only when no watermark file exists yet (the very first run, or
# one where the file was deleted). Every run after that uses the watermark instead.
cLookbackDays_int = 14
# Most issues-with-new-comments reported per pass; newest first.
cNewCommentsMax_int = 20
# How many Do Now items a single run may be given. Board order decides which one.
cDoNowPerRun_int = 1
# ASSIGNMENT GATE (Jeremy's rule, 2026-09-05). The cron works only Issues assigned to the
# bot account. A Do Now item not assigned to it is left exactly where it is and reported
# separately so the log can say so. A mention on an unassigned Issue is still passed
# through, flagged, so the run can reply that it is not assigned rather than work it.

# LABEL OVERRIDE (Jeremy's rule, 2026-09-06). A Do Now item labeled "fh" or "sh" overrides
# the cron's default launch profile for that item only; run-cron.ps1 reads the "profile"
# reported below and resolves it to --model/--effort flags. Neither label present means no
# override (the item launches at the cron default). Both present is a conflict: treated as
# no override, and the issue number is reported separately so the wrapper can log it rather
# than silently pick one.
cProfileLabels_arr = ("fh", "sh")


def fnGh(pArgs_arr):
    # Runs a gh command and returns parsed JSON. Raises on failure so the caller
    # can distinguish a broken detection from a genuinely empty one.
    vProc_obj = subprocess.run(
        ["gh"] + pArgs_arr,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if vProc_obj.returncode != 0:
        raise RuntimeError("gh " + " ".join(pArgs_arr) + " failed: " + vProc_obj.stderr.strip())
    vOut_str = vProc_obj.stdout.strip()
    return json.loads(vOut_str) if vOut_str else None


def fnFetchBoard_arr():
    # Lean board read: one GraphQL point per page of 100 items, against about 101 for
    # `gh project item-list`, which asks for every field of every item. Returns items in
    # board order shaped like item-list's output, so callers did not have to change:
    #   {"id", "status", "title", "assignees": [logins], "content": {"number", "url"}}
    cQuery_str = (
        'query($after:String){user(login:"' + cOwner_str + '"){projectV2(number:' + str(cProject_int) + '){'
        'items(first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{id '
        'fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}} '
        'content{... on Issue{number title url assignees(first:10){nodes{login}} '
        'labels(first:20){nodes{name}}}}}}}}}'
    )
    vOut_arr = []
    vAfter_str = None
    while True:
        vArgs_arr = ["api", "graphql", "-f", "query=" + cQuery_str]
        if vAfter_str:
            vArgs_arr += ["-f", "after=" + vAfter_str]
        vData_obj = fnGh(vArgs_arr)
        vItems_obj = vData_obj["data"]["user"]["projectV2"]["items"]
        for vNode_obj in vItems_obj.get("nodes", []):
            vContent_obj = vNode_obj.get("content") or {}
            vStatus_obj = vNode_obj.get("fieldValueByName") or {}
            vOut_arr.append({
                "id":        vNode_obj.get("id"),
                "status":    vStatus_obj.get("name", ""),
                "title":     vContent_obj.get("title", ""),
                "assignees": [a.get("login", "") for a in ((vContent_obj.get("assignees") or {}).get("nodes") or [])],
                "labels":    [l.get("name", "") for l in ((vContent_obj.get("labels") or {}).get("nodes") or [])],
                "content":   {"number": vContent_obj.get("number"), "url": vContent_obj.get("url", "")},
            })
        if not vItems_obj.get("pageInfo", {}).get("hasNextPage"):
            break
        vAfter_str = vItems_obj["pageInfo"]["endCursor"]
    return vOut_arr


def fnProfileFromLabels_str(pLabels_arr):
    # Resolves an item's board labels to a launch-profile override: "fh", "sh", or None
    # (neither label present -- launch at the cron default). Both present at once is a
    # conflict, reported to the caller separately rather than guessing which one wins.
    vNames_arr = [l.lower() for l in pLabels_arr]
    vHasFh_bol = "fh" in vNames_arr
    vHasSh_bol = "sh" in vNames_arr
    if vHasFh_bol and vHasSh_bol:
        return None, True
    if vHasSh_bol:
        return "sh", False
    if vHasFh_bol:
        return "fh", False
    return None, False


def fnFindDoNow():
    # Gated purely on the board Status field. The issue's Open/Closed flag is not
    # consulted: Status is the single source of truth for this board, so a "Do Now"
    # item is work regardless of the Open/Closed flag (which is kept uniformly Open
    # and therefore carries no meaning here).
    #
    # Returns every Do Now item in board order. fnMain trims the list to
    # cDoNowPerRun_int; the full list is kept here so the queue depth can be reported.
    vItems_arr = fnFetchBoard_arr()

    vOut_arr = []
    vUnassigned_arr = []
    vLabelConflicts_arr = []
    vStatusByNumber_obj = {}
    for vItem_obj in vItems_arr:
        vItemNumber_int = (vItem_obj.get("content") or {}).get("number")
        if vItemNumber_int is not None:
            vStatusByNumber_obj[vItemNumber_int] = vItem_obj.get("status", "")
        if vItem_obj.get("status") != cDoNowStatus:
            continue
        vContent_obj = vItem_obj.get("content", {}) or {}
        vAssignees_arr = [a.lower() for a in (vItem_obj.get("assignees") or [])]
        if cBotLogin_str.lower() not in vAssignees_arr:
            vUnassigned_arr.append(vContent_obj.get("number"))
            continue
        vProfile_str, vConflict_bol = fnProfileFromLabels_str(vItem_obj.get("labels") or [])
        if vConflict_bol:
            vLabelConflicts_arr.append(vContent_obj.get("number"))
        vOut_arr.append({
            "number":  vContent_obj.get("number"),
            "title":   vItem_obj.get("title", ""),
            "url":     vContent_obj.get("url", ""),
            "profile": vProfile_str,
        })
    return vOut_arr, vUnassigned_arr, vLabelConflicts_arr, vStatusByNumber_obj


def fnIsClaudeComment(pComment_obj):
    # True if this comment was written by Claude rather than by Jeremy. The bot account
    # is the reliable signal; the hidden marker is the legacy fallback for comments made
    # back when Claude posted under Jeremy's own account. REST comment objects carry the
    # author at .user.login; the older GraphQL shape carried it at .author.login, and
    # both are read so either source works.
    vUser_obj = pComment_obj.get("user") or pComment_obj.get("author") or {}
    vAuthor_str = (vUser_obj.get("login") or "").lower()
    if vAuthor_str == cBotLogin_str.lower():
        return True
    return cMarker_str in (pComment_obj.get("body") or "")


def fnGhPaginated(pPath_str):
    # gh api --paginate prints one JSON array per page, back to back. Decode them all.
    vProc_obj = subprocess.run(
        ["gh", "api", "--paginate", pPath_str],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if vProc_obj.returncode != 0:
        raise RuntimeError("gh api --paginate " + pPath_str + " failed: " + vProc_obj.stderr.strip())
    vOut_arr = []
    vDecoder_obj = json.JSONDecoder()
    vText_str = vProc_obj.stdout.strip()
    vPos_int = 0
    while vPos_int < len(vText_str):
        vPage_obj, vPos_int = vDecoder_obj.raw_decode(vText_str, vPos_int)
        vOut_arr.extend(vPage_obj if isinstance(vPage_obj, list) else [vPage_obj])
        while vPos_int < len(vText_str) and vText_str[vPos_int].isspace():
            vPos_int += 1
    return vOut_arr


def fnLoadWatermark_str():
    # The newest comment timestamp seen by the last successful run, or None the first
    # time this ever runs (or if the file is missing, empty, or unreadable). None tells
    # the caller to fall back to the fixed bootstrap window instead.
    if not cWatermarkFile_pth.exists():
        return None
    try:
        vData_obj = json.loads(cWatermarkFile_pth.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return vData_obj.get("since") or None


def fnSaveWatermark(pSince_str):
    # Called only after a full run completes without raising (see fnMain), so a run that
    # fails partway leaves the watermark where it was; the next run re-covers the same
    # ground instead of silently skipping whatever the failed run didn't finish reading.
    cWatermarkFile_pth.write_text(json.dumps({"since": pSince_str}), encoding="utf-8")


def fnFindMentions(pStatusByNumber_obj):
    # Cheap mention scan (2026-09-05, watermarked 2026-09-06). One REST call lists every
    # comment in the repo since the persisted watermark, regardless of the issue's
    # Open/Closed state, for about one point per page of the separate 5,000-per-hour REST
    # budget. The old scan pulled all 500 issues with every comment through GraphQL at
    # about 100 points a pass, which with two crons polling every five minutes exhausted
    # the bot's GraphQL budget.
    #
    # A fixed lookback window (the original fix) goes blind on any mention older than the
    # window, which matters when Jeremy is away for a few weeks rather than a few days. A
    # persisted watermark has no such ceiling: the window since the last successful run
    # is scanned in full, however long that gap turns out to be, and pagination is what
    # already absorbs a run that comes back to a larger-than-usual pile of comments.
    #
    # Dedup works inside the window: a reply from Claude is always newer than the mention
    # it answers, so if the mention is in the window, so is the reply. The one gap left is
    # a mention older than the watermark from before this feature existed, or from before
    # the very first run; Jeremy re-mentions in that case, same as the old fixed window.
    # Assignees are fetched only for the few issues that actually carry a fresh mention.
    vSince_str = fnLoadWatermark_str()
    if not vSince_str:
        vBootstrap_dte = datetime.now(timezone.utc) - timedelta(days=cLookbackDays_int)
        vSince_str = vBootstrap_dte.strftime("%Y-%m-%dT%H:%M:%SZ")
    vComments_arr = fnGhPaginated(
        "repos/" + cRepo_str + "/issues/comments?since=" + vSince_str + "&per_page=100"
    )

    # Next run's watermark: the newest created_at seen this pass, or "now" if nothing came
    # back at all. GitHub's since filter is inclusive of the exact second given, so storing
    # the newest timestamp seen (rather than one second past it) risks re-fetching that one
    # comment next time rather than risking a skip -- harmless, since a re-seen comment is
    # just re-evaluated by the per-issue dedup above, not re-reported as new.
    vNewWatermark_str = max(
        (c.get("created_at", "") for c in vComments_arr), default=""
    ) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Group by issue number, taken from the comment's issue_url.
    vByIssue_obj = {}
    for vComment_obj in vComments_arr:
        vIssueUrl_str = vComment_obj.get("issue_url") or ""
        try:
            vNumber_int = int(vIssueUrl_str.rstrip("/").rsplit("/", 1)[1])
        except (ValueError, IndexError):
            continue
        vByIssue_obj.setdefault(vNumber_int, []).append(vComment_obj)

    vOut_arr = []
    vNewComments_arr = []
    for vNumber_int, vIssueComments_arr in vByIssue_obj.items():
        # Timestamp of the most recent comment Claude left on this issue in the window.
        vLastHandled_str = ""
        for vComment_obj in vIssueComments_arr:
            if fnIsClaudeComment(vComment_obj):
                vCreated_str = vComment_obj.get("created_at", "")
                if vCreated_str > vLastHandled_str:
                    vLastHandled_str = vCreated_str

        # Human comments newer than Claude's last word on the issue. Those that mention
        # the bot are requests; the rest are information the run should read (Jeremy's
        # rule, 2026-09-06) but not a trigger on their own.
        vFresh_arr = []
        vUnmentioned_arr = []
        for vComment_obj in vIssueComments_arr:
            if fnIsClaudeComment(vComment_obj):
                continue  # one of Claude's own comments, not a request
            vCreated_str = vComment_obj.get("created_at", "")
            if vLastHandled_str and vCreated_str <= vLastHandled_str:
                continue  # already handled: a Claude comment exists after it
            vBody_str = vComment_obj.get("body") or ""
            if any(m in vBody_str.lower() for m in cMentions_arr):
                vFresh_arr.append(vComment_obj)
            else:
                vUnmentioned_arr.append(vComment_obj)
        if not vFresh_arr:
            if vUnmentioned_arr:
                vLatest_obj = max(vUnmentioned_arr, key=lambda c: c.get("created_at", ""))
                vNewComments_arr.append({
                    "issue":      vNumber_int,
                    "status":     pStatusByNumber_obj.get(vNumber_int, ""),
                    "count":      len(vUnmentioned_arr),
                    "latest_at":  vLatest_obj.get("created_at", ""),
                    "url":        vLatest_obj.get("html_url", ""),
                    "snippet":    " ".join((vLatest_obj.get("body") or "").split())[:200],
                })
            continue

        # Assignment gate: one small REST read, only for issues with a fresh mention.
        vIssue_obj = fnGh(["api", "repos/" + cRepo_str + "/issues/" + str(vNumber_int)]) or {}
        vAssignees_arr = [(a.get("login") or "").lower() for a in (vIssue_obj.get("assignees") or [])]
        vAssignedToBot_bol = cBotLogin_str.lower() in vAssignees_arr

        for vComment_obj in vFresh_arr:
            vBody_str = vComment_obj.get("body") or ""
            vOut_arr.append({
                "issue":      vNumber_int,
                "status":     pStatusByNumber_obj.get(vNumber_int, ""),
                "comment_id": vComment_obj.get("id"),
                "created_at": vComment_obj.get("created_at", ""),
                "url":        vComment_obj.get("html_url", ""),
                "snippet":    " ".join(vBody_str.split())[:200],
                "assigned_to_bot": vAssignedToBot_bol,
            })

    vNewComments_arr.sort(key=lambda c: c["latest_at"], reverse=True)
    return vOut_arr, vNewComments_arr[:cNewCommentsMax_int], vNewWatermark_str


def fnMain():
    try:
        vAllDoNow_arr, vUnassigned_arr, vLabelConflicts_arr, vStatusByNumber_obj = fnFindDoNow()
        vMentions_arr, vNewComments_arr, vNewWatermark_str = fnFindMentions(vStatusByNumber_obj)
        # Saved only now that both reads above have succeeded in full.
        fnSaveWatermark(vNewWatermark_str)
    except Exception as vError_obj:
        print(json.dumps({"ok": False, "error": str(vError_obj), "has_work": False}))
        return

    # One item per run. The first in board order goes out; the rest wait for later ticks.
    vDoNow_arr   = vAllDoNow_arr[:cDoNowPerRun_int]
    vWaiting_int = len(vAllDoNow_arr) - len(vDoNow_arr)

    vResult_obj = {
        "ok": True,
        "has_work": bool(vDoNow_arr) or bool(vMentions_arr),
        "do_now": vDoNow_arr,
        "do_now_waiting": vWaiting_int,
        # Every Do Now item in board order. The wrapper uses this after a run to hand
        # back any item the run picked up and left In Progress.
        "do_now_queue": [i["number"] for i in vAllDoNow_arr],
        # Do Now items not assigned to the bot. Never worked, never touched; listed so the
        # wrapper can log that they were seen and left alone.
        "do_now_unassigned": vUnassigned_arr,
        # Do Now items carrying both the "fh" and "sh" labels at once: the override is
        # ambiguous, so it is ignored (the item launches at the cron default) and the
        # number is listed here so the wrapper can log the conflict for Jeremy to fix.
        "do_now_label_conflicts": vLabelConflicts_arr,
        "mentions": vMentions_arr,
        # Issues with new human comments that do not mention the bot: context to read,
        # not work to pick up, and not a trigger by themselves.
        "new_comments": vNewComments_arr,
    }
    print(json.dumps(vResult_obj, indent=2))


fnMain()
