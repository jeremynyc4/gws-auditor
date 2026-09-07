#!/usr/bin/env python3
# >>>>> set-status.py <<<<<
# Reads or sets the GitHub Project #5 "Status" of the board item backing an issue.
# Self-contained: looks up the project, the Status field, and the option id by name at
# run time, so it keeps working even if option ids change. Used by the cron wrapper and
# the runbook.
#
# Usage:
#   python set-status.py --list                            -> "number<TAB>status" per board item
#   python set-status.py <issue> --get                     -> prints the current status
#   python set-status.py <issue> "<status>"                -> sets it
#   python set-status.py <issue> "<status>" "<if_current>" -> sets it ONLY if the current
#                                                             status equals <if_current>
#
# An issue that is not on the board is a graceful no-op (exit 0), so the cron can call
# this on any issue without having to special-case board membership.

import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cOwner_str   = "jeremynyc4"
cProject_int = 5

# HARD PROHIBITION. Approval is Jeremy's sign-off and is never Claude's to write --
# not to grant it, and not even to "restore" one that was overwritten. If an approval
# gets clobbered, the correct move is to tell Jeremy and let him re-approve. Any status
# containing "approved" is refused here so no code path of mine can set one.
cForbiddenSubstring_str = "approved"


def fnGh(pArgs_arr):
    vProc_obj = subprocess.run(["gh"] + pArgs_arr, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    if vProc_obj.returncode != 0:
        raise RuntimeError("gh failed: " + vProc_obj.stderr.strip())
    return vProc_obj.stdout.strip()


def fnFetchBoard_arr():
    # Lean board read: one GraphQL point per page of 100 items, against about 101 for
    # `gh project item-list`, which asks for every field of every item. Returns items in
    # board order shaped like item-list's output, so callers did not have to change:
    #   {"id", "status", "title", "assignees": [logins], "content": {"number", "url"}}
    cQuery_str = (
        'query($after:String){user(login:"' + cOwner_str + '"){projectV2(number:' + str(cProject_int) + '){'
        'items(first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{id '
        'fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}} '
        'content{... on Issue{number title url assignees(first:10){nodes{login}}}}}}}}}'
    )
    vOut_arr = []
    vAfter_str = None
    while True:
        vArgs_arr = ["api", "graphql", "-f", "query=" + cQuery_str]
        if vAfter_str:
            vArgs_arr += ["-f", "after=" + vAfter_str]
        vData_obj = json.loads(fnGh(vArgs_arr))
        vItems_obj = vData_obj["data"]["user"]["projectV2"]["items"]
        for vNode_obj in vItems_obj.get("nodes", []):
            vContent_obj = vNode_obj.get("content") or {}
            vStatus_obj = vNode_obj.get("fieldValueByName") or {}
            vOut_arr.append({
                "id":        vNode_obj.get("id"),
                "status":    vStatus_obj.get("name", ""),
                "title":     vContent_obj.get("title", ""),
                "assignees": [a.get("login", "") for a in ((vContent_obj.get("assignees") or {}).get("nodes") or [])],
                "content":   {"number": vContent_obj.get("number"), "url": vContent_obj.get("url", "")},
            })
        if not vItems_obj.get("pageInfo", {}).get("hasNextPage"):
            break
        vAfter_str = vItems_obj["pageInfo"]["endCursor"]
    return vOut_arr


def fnFindItem(pIssue_int):
    for vItem_obj in fnFetchBoard_arr():
        if (vItem_obj.get("content") or {}).get("number") == pIssue_int:
            return vItem_obj
    return None


def fnMain():
    if len(sys.argv) < 2 or (len(sys.argv) < 3 and sys.argv[1] != "--list"):
        print("usage: set-status.py --list | <issue> --get | <issue> \"<status>\" [\"<if_current>\"]")
        sys.exit(2)

    # --list: one line per board item, "number<TAB>status", in board order. Used by the
    # cron wrapper to reconcile after a run, so it never has to pass a GraphQL query
    # through PowerShell's native-argument quoting.
    if sys.argv[1] == "--list":
        for vItem_obj in fnFetchBoard_arr():
            vNumber_int = (vItem_obj.get("content") or {}).get("number")
            if vNumber_int is not None:
                print(str(vNumber_int) + "\t" + vItem_obj.get("status", ""))
        return

    vIssue_int = int(sys.argv[1])
    vItem_obj  = fnFindItem(vIssue_int)

    # --get: print the current status and stop.
    if sys.argv[2] == "--get":
        print(vItem_obj.get("status", "") if vItem_obj else "")
        return

    vStatus_str    = sys.argv[2]
    vIfCurrent_str = sys.argv[3] if len(sys.argv) > 3 else None

    # Refuse to write an approval status, always.
    if cForbiddenSubstring_str in vStatus_str.lower():
        print("REFUSED: '" + vStatus_str + "' is Jeremy's sign-off and is never set by Claude. "
              "If an approval was lost, tell Jeremy and let him re-approve.")
        sys.exit(3)

    if not vItem_obj:
        print("issue #" + str(vIssue_int) + " not on the board; skipped")
        return

    vCurrent_str = vItem_obj.get("status", "")

    # The other half of the prohibition. Refusing to *set* "approved" was not enough:
    # an approval can also be destroyed by moving the item AWAY from it, which is exactly
    # how #63 got clobbered. "Approved by Jeremy" is Jeremy's, so once an item carries it,
    # no code path of mine may change it at all -- only Jeremy, in the board UI, moves it
    # off. If the cron genuinely needs to act on an approved item, it stops and says so.
    if cForbiddenSubstring_str in vCurrent_str.lower():
        print("REFUSED: issue #" + str(vIssue_int) + " is '" + vCurrent_str
              + "', Jeremy's sign-off. My code does not change an approved item -- "
              "only Jeremy does. Leaving it untouched.")
        sys.exit(3)

    if vIfCurrent_str is not None and vCurrent_str != vIfCurrent_str:
        print("issue #" + str(vIssue_int) + " status is '" + vCurrent_str
              + "' (not '" + vIfCurrent_str + "'); skipped")
        return

    if vCurrent_str == vStatus_str:
        print("issue #" + str(vIssue_int) + " already '" + vStatus_str + "'")
        return

    # Resolve the project id and the Status field's id + the target option id, by name.
    vQuery_str = (
        'query{user(login:"' + cOwner_str + '"){projectV2(number:' + str(cProject_int) + '){'
        'id field(name:"Status"){... on ProjectV2SingleSelectField{id options{id name}}}}}}'
    )
    vData_obj = json.loads(fnGh(["api", "graphql", "-f", "query=" + vQuery_str]))
    vProject_obj  = vData_obj["data"]["user"]["projectV2"]
    vField_obj    = vProject_obj["field"]

    vOptionId_str = None
    for vOption_obj in vField_obj["options"]:
        if vOption_obj["name"] == vStatus_str:
            vOptionId_str = vOption_obj["id"]
            break
    if not vOptionId_str:
        vNames_arr = [o["name"] for o in vField_obj["options"]]
        raise RuntimeError("no status named '" + vStatus_str + "'. Options: " + ", ".join(vNames_arr))

    vMutation_str = (
        'mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){'
        'updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,'
        'value:{singleSelectOptionId:$o}}){projectV2Item{id}}}'
    )
    fnGh([
        "api", "graphql",
        "-f", "p=" + vProject_obj["id"],
        "-f", "i=" + vItem_obj["id"],
        "-f", "f=" + vField_obj["id"],
        "-f", "o=" + vOptionId_str,
        "-f", "query=" + vMutation_str,
    ])
    print("issue #" + str(vIssue_int) + "  " + (vCurrent_str or "(none)") + " -> " + vStatus_str)


fnMain()
