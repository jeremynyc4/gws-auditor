#!/usr/bin/env python3
# >>>>> fnSplitIssue.py <<<<<
# Converts the unchecked checklist items of a GitHub Issue into linked sub-Issues,
# per Jeremy's convention: sub-Issues instead of checkbox lists, numbered lists,
# progress in comments. Each sub-Issue gets the parent's milestone and assignees,
# is linked as a GitHub sub-issue of the parent, is added to the project board
# directly after the parent (in checklist order), and is set to Backlog. The
# converted lines are removed from the parent's description (the sub-Issues now
# carry them) and a numbered summary is posted as a comment on the parent.
#
# Runs as whoever GH_TOKEN says; the wrapper and the skill set it to the bot.
#
# Usage:
#   python fnSplitIssue.py <issue> --plan     -> print what would happen, change nothing
#   python fnSplitIssue.py <issue>            -> do it
#   python fnSplitIssue.py <issue> --project 4   (override the board number)
#   python fnSplitIssue.py <issue> --titles titles.json
#       titles.json maps item number (1-based, unchecked items only) to a title
#       written by hand for items whose text is too long to be a title. Items
#       not in the file get a title built by fnTitle_str.
#
# The board number defaults to cProject_int in detect-work.py beside this file.

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cBotLogin_str = "claudebot-ymerej"
cOwner_str = "jeremynyc4"
cBacklogStatus_str = "Backlog"
cMaxTitle_int = 100
cMinTitle_int = 20
cAlreadyOnBoard_str = "Content already exists in this project"
cBoardLookupTries_int = 6
cBoardLookupPause_flt = 2.0
cChecklistPattern_obj = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.+?)\s*$")
cSubBodyPattern_obj = re.compile(r"^Sub-Issue (\d+) of #(\d+)\b")


def fnGh_str(pArgs_arr):
  vProc_obj = subprocess.run(["gh"] + pArgs_arr, capture_output=True, text=True, encoding="utf-8", errors="replace")

  if vProc_obj.returncode != 0:
    raise RuntimeError("gh " + " ".join(pArgs_arr[:3]) + " failed: " + vProc_obj.stderr.strip())

  return vProc_obj.stdout.strip()


# >>>>> fnRepo_str <<<<<
# The owner/name of the repo the current directory belongs to.
def fnRepo_str():
  return fnGh_str(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])


# >>>>> fnDefaultProject_int <<<<<
# Reads cProject_int from detect-work.py next to this script, so each repo's
# automation folder carries its own board number.
def fnDefaultProject_int():
  cDetect_obj = Path(__file__).with_name("detect-work.py")

  if cDetect_obj.exists():
    vMatch_obj = re.search(r"cProject_int\s*=\s*(\d+)", cDetect_obj.read_text(encoding="utf-8"))

    if vMatch_obj:
      return int(vMatch_obj.group(1))

  raise RuntimeError("no --project given and no cProject_int in detect-work.py")


# >>>>> fnParseChecklist_arr <<<<<
# Returns [(line_index, checked, text)] for every checklist line in the body.
def fnParseChecklist_arr(pBody_str):
  vItems_arr = []

  for vIndex_int, vLine_str in enumerate(pBody_str.splitlines()):
    vMatch_obj = cChecklistPattern_obj.match(vLine_str)

    if vMatch_obj:
      vItems_arr.append((vIndex_int, vMatch_obj.group(1).lower() == "x", vMatch_obj.group(2)))

  return vItems_arr


# >>>>> fnTitle_str <<<<<
# A sub-Issue title. Short items are used as they are, minus a trailing period.
# Long items are never cut mid-phrase: the title is rebuilt from the item's own
# leading clause (the part before a colon, semicolon, parenthesis, or the first
# comma), so it always reads as a complete instruction. The full text goes in
# the body regardless. Hand-written titles from --titles take precedence.
def fnTitle_str(pText_str):
  vText_str = pText_str.strip().rstrip(".")

  if len(vText_str) <= cMaxTitle_int:
    return vText_str

  for vSeparator_str in (":", ";", " (", ", "):
    vHead_str = vText_str.split(vSeparator_str, 1)[0].strip().rstrip(".,;:")

    if cMinTitle_int <= len(vHead_str) <= cMaxTitle_int:
      return vHead_str

  # No usable clause boundary: keep whole words up to the limit and say so.
  vWords_arr = vText_str.split(" ")
  vHead_str = ""

  for vWord_str in vWords_arr:
    if len(vHead_str) + len(vWord_str) + 1 > cMaxTitle_int:
      break

    vHead_str = (vHead_str + " " + vWord_str).strip()

  return vHead_str.rstrip(".,;:") + " (see body)"


# >>>>> fnHandTitles_obj <<<<<
# Loads --titles <file>: {"3": "Implement the chars command", ...}.
def fnHandTitles_obj():
  if "--titles" not in sys.argv:
    return {}

  cPath_obj = Path(sys.argv[sys.argv.index("--titles") + 1])

  return {int(vKey_str): vValue_str for vKey_str, vValue_str in json.loads(cPath_obj.read_text(encoding="utf-8")).items()}


# >>>>> fnBoardItemId_str <<<<<
# The project item id for one issue number, read from the issue's own project
# items rather than by listing the whole board: a full item-list costs hundreds
# of GraphQL points and exhausted the bot's hourly quota mid-run on 2026-09-05.
# The board's auto-add rule places new Issues a moment after they are created
# and the connection can lag behind it, so this looks a few times before giving up.
def fnBoardItemId_str(pRepo_str, pProject_int, pNumber_int):
  vOwner_str, vName_str = pRepo_str.split("/", 1)
  vQuery_str = ('query{repository(owner:"' + vOwner_str + '",name:"' + vName_str + '"){issue(number:' + str(pNumber_int) + '){'
                'projectItems(first:20){nodes{id project{number}}}}}}')

  for vTry_int in range(cBoardLookupTries_int):
    vNodes_arr = json.loads(fnGh_str(["api", "graphql", "-f", "query=" + vQuery_str]))["data"]["repository"]["issue"]["projectItems"]["nodes"]

    for vNode_obj in vNodes_arr:
      if vNode_obj["project"]["number"] == pProject_int:
        return vNode_obj["id"]

    time.sleep(cBoardLookupPause_flt)

  raise RuntimeError(f"#{pNumber_int} is on the board but its project item never appeared")


# >>>>> fnExistingSubIssues_obj <<<<<
# Maps item number -> (issue number, title, url) for sub-Issues an earlier,
# interrupted run already created under this parent, recognised by the
# "Sub-Issue k of #N" line the script writes at the top of each body. Lets a
# rerun pick up where it stopped instead of creating duplicates.
def fnExistingSubIssues_obj(pRepo_str, pIssue_int):
  vExisting_obj = {}
  vSubs_arr = json.loads(fnGh_str(["api", f"repos/{pRepo_str}/issues/{pIssue_int}/sub_issues"]))

  for vSub_obj in vSubs_arr:
    vMatch_obj = cSubBodyPattern_obj.match(vSub_obj.get("body") or "")

    if vMatch_obj and int(vMatch_obj.group(2)) == pIssue_int:
      vExisting_obj[int(vMatch_obj.group(1))] = (vSub_obj["number"], vSub_obj["title"], vSub_obj["html_url"])

  return vExisting_obj


# >>>>> fnProjectIds_obj <<<<<
# The project's node id, its Status field id, and the Backlog option id.
def fnProjectIds_obj(pProject_int):
  vQuery_str = ('query{user(login:"' + cOwner_str + '"){projectV2(number:' + str(pProject_int) + '){'
                'id field(name:"Status"){... on ProjectV2SingleSelectField{id options{id name}}}}}}')
  vProject_obj = json.loads(fnGh_str(["api", "graphql", "-f", "query=" + vQuery_str]))["data"]["user"]["projectV2"]
  vOption_str = next(vOption_obj["id"] for vOption_obj in vProject_obj["field"]["options"] if vOption_obj["name"] == cBacklogStatus_str)

  return {"project": vProject_obj["id"], "field": vProject_obj["field"]["id"], "backlog": vOption_str}


def fnMain():
  if len(sys.argv) < 2 or not sys.argv[1].isdigit():
    print(__doc__ or "usage: fnSplitIssue.py <issue> [--plan] [--project N]")
    sys.exit(2)

  cIssue_int = int(sys.argv[1])
  cPlan_bol = "--plan" in sys.argv
  cProject_int = int(sys.argv[sys.argv.index("--project") + 1]) if "--project" in sys.argv else fnDefaultProject_int()
  cRepo_str = fnRepo_str()

  vParent_obj = json.loads(fnGh_str(["issue", "view", str(cIssue_int), "--json",
                                      "number,title,body,milestone,assignees,url"]))
  vBody_str = vParent_obj.get("body") or ""
  vItems_arr = fnParseChecklist_arr(vBody_str)
  vTodo_arr = [vItem_obj for vItem_obj in vItems_arr if not vItem_obj[1]]
  vDone_arr = [vItem_obj for vItem_obj in vItems_arr if vItem_obj[1]]
  vMilestone_str = (vParent_obj.get("milestone") or {}).get("title", "")
  vAssignees_arr = [vAssignee_obj["login"] for vAssignee_obj in vParent_obj.get("assignees", [])]
  # Every Issue the bot creates is assigned to the bot by default (Jeremy's rule,
  # 2026-09-05), whether or not the parent was.
  if cBotLogin_str not in vAssignees_arr:
    vAssignees_arr.append(cBotLogin_str)

  print(f"#{cIssue_int} {vParent_obj['title']}")
  print(f"  milestone: {vMilestone_str or '(none)'}   assignees: {', '.join(vAssignees_arr) or '(none)'}")
  print(f"  checklist items: {len(vItems_arr)} ({len(vTodo_arr)} unchecked to split, {len(vDone_arr)} already checked, left as text)")

  if not vTodo_arr:
    print("nothing to split")
    return

  vHand_obj = fnHandTitles_obj()

  for vIndex_int, (vLine_int, vChecked_bol, vText_str) in enumerate(vTodo_arr, 1):
    vFlag_str = " [hand]" if vIndex_int in vHand_obj else (" [built]" if len(vText_str.rstrip(".")) > cMaxTitle_int else "")
    print(f"  {vIndex_int}. {vHand_obj.get(vIndex_int, fnTitle_str(vText_str))}{vFlag_str}")

  if cPlan_bol:
    print("plan only; nothing changed")
    return

  vIds_obj = fnProjectIds_obj(cProject_int)

  # A parent that is not on the board gives no anchor; the sub-Issues then keep the board's default order.
  try:
    vAfter_str = fnBoardItemId_str(cRepo_str, cProject_int, cIssue_int)
  except RuntimeError:
    vAfter_str = None

  vExisting_obj = fnExistingSubIssues_obj(cRepo_str, cIssue_int)
  vCreated_arr = []

  for vIndex_int, (vLine_int, vChecked_bol, vText_str) in enumerate(vTodo_arr, 1):
    if vIndex_int in vExisting_obj:
      # An interrupted run already created and linked this one; only the board steps remain.
      vNumber_int, vTitle_str, vUrl_str = vExisting_obj[vIndex_int]
      print(f"  reusing #{vNumber_int} from an earlier run")
    else:
      vTitle_str = vHand_obj.get(vIndex_int, fnTitle_str(vText_str))
      vSubBody_str = f"Sub-Issue {vIndex_int} of #{cIssue_int} ({vParent_obj['title']}).\n\n{vText_str}\n"
      vArgs_arr = ["issue", "create", "--title", vTitle_str, "--body", vSubBody_str]

      if vMilestone_str:
        vArgs_arr += ["--milestone", vMilestone_str]

      for vAssignee_str in vAssignees_arr:
        vArgs_arr += ["--assignee", vAssignee_str]

      vUrl_str = fnGh_str(vArgs_arr).splitlines()[-1]
      vNumber_int = int(vUrl_str.rstrip("/").rsplit("/", 1)[1])
      vSubDbId_int = json.loads(fnGh_str(["api", f"repos/{cRepo_str}/issues/{vNumber_int}"]))["id"]

      # Link as a real GitHub sub-issue of the parent.
      fnGh_str(["api", "-X", "POST", f"repos/{cRepo_str}/issues/{cIssue_int}/sub_issues",
                "-F", f"sub_issue_id={vSubDbId_int}"])

    # Put it on the board right after the parent (or after the previous sub-issue), in Backlog.
    # The board's auto-add rule can place a new Issue before this script does; when it has,
    # GitHub refuses the add, so look the existing item up by number instead of failing.
    try:
      vItemId_str = json.loads(fnGh_str(["project", "item-add", str(cProject_int), "--owner", cOwner_str,
                                          "--url", vUrl_str, "--format", "json"]))["id"]
    except RuntimeError as vError_obj:
      if cAlreadyOnBoard_str not in str(vError_obj):
        raise
      vItemId_str = fnBoardItemId_str(cRepo_str, cProject_int, vNumber_int)
    fnGh_str(["api", "graphql",
              "-f", "p=" + vIds_obj["project"], "-f", "i=" + vItemId_str, "-f", "f=" + vIds_obj["field"], "-f", "o=" + vIds_obj["backlog"],
              "-f", "query=mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){projectV2Item{id}}}"])

    if vAfter_str:
      fnGh_str(["api", "graphql", "-f", "p=" + vIds_obj["project"], "-f", "i=" + vItemId_str, "-f", "a=" + vAfter_str,
                "-f", "query=mutation($p:ID!,$i:ID!,$a:ID!){updateProjectV2ItemPosition(input:{projectId:$p,itemId:$i,afterId:$a}){clientMutationId}}"])
      vAfter_str = vItemId_str

    vCreated_arr.append((vIndex_int, vNumber_int, vTitle_str, vUrl_str))
    print(f"  created #{vNumber_int}: {vTitle_str}")

  # Take the converted lines out of the parent description; keep everything else.
  vDropLines_arr = {vLine_int for vLine_int, vChecked_bol, vText_str in vTodo_arr}
  vNewBody_str = "\n".join(vLine_str for vIndex_int, vLine_str in enumerate(vBody_str.splitlines()) if vIndex_int not in vDropLines_arr)
  vNewBody_str = re.sub(r"\n{3,}", "\n\n", vNewBody_str).strip() + "\n\nSubtasks are sub-Issues; see the sidebar and the comment listing them.\n"
  fnGh_str(["issue", "edit", str(cIssue_int), "--body", vNewBody_str])

  vComment_str = "Split the checklist into sub-Issues:\n\n" + "\n".join(
    f"{vIndex_int}. #{vNumber_int} {vTitle_str}" for vIndex_int, vNumber_int, vTitle_str, vUrl_str in vCreated_arr)

  if vDone_arr:
    vComment_str += "\n\nAlready-checked items were left in the description as done, not split."

  fnGh_str(["issue", "comment", str(cIssue_int), "--body", vComment_str])
  print(f"done: {len(vCreated_arr)} sub-Issues under #{cIssue_int}")


fnMain()
