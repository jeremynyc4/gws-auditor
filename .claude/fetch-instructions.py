"""SessionStart hook: give a session that has no laptop-level CLAUDE.md the same
standing instructions the laptop gets.

On Jeremy's laptop the user-level hook already rebuilds ~/.claude/CLAUDE.md from
the published Coding Style Guide and General AI Instructions, so this script
writes a one-line stub and stops; importing the full text again would put it in
context twice. In a cloud sandbox (an iOS remote session, a cloud routine,
Claude Code on the web) there is no user-level file, so this script fetches both
Docs into .claude/instructions.md, which CLAUDE.md imports. The file is
gitignored: it is a cache of the Docs, not project history.

Some cloud sandboxes block outbound HTTP to docs.google.com under an
organization network policy (confirmed 2026-09-06 against a live routine: the
fetch fails with "Tunnel connection failed: 403 Forbidden" / "connect_rejected
(organization policy)"). When that happens this script writes a fallback stub
instead of leaving the file missing. The fallback tells the agent to read the
same two Docs through the Google Drive connector instead of over HTTP, since a
connector call goes through Anthropic's own infrastructure rather than the
sandbox's blocked egress. That only works if the session actually has Drive
connector access and chooses to act on the instruction -- weaker than a hook,
but better than nothing. If the environment's network policy is ever loosened
to allow docs.google.com, this fallback stops being needed and quietly stops
firing on its own.

Usage:
  python3 "fetch-instructions.py"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fnPublishedDocToText as conv

# Constants are stored here, as the document list is the part that changes.
cDocs_arr = [
  ("Coding Style Guide", "https://docs.google.com/document/d/e/2PACX-1vQroXY9PmBLPWkG0AaOBY-34AypGgYqIP6AIorPuvS5Pm7Db_ilnzJfb8ggVhgsyBx5Yt64b7HOntL-/pub"),
  ("General AI Instructions", "https://docs.google.com/document/d/e/2PACX-1vSem3ZWy3ryFhCUdYhwWxL_q4tcX5BGCWNQGEXD7R1MY3qPSJYO33BxWzjSpmhGqDapFA4oa23JULJg/pub"),
]
# (title, Drive file ID) for the fallback stub -- the file ID, not the published
# URL above, since the fallback tells the agent to use the Drive connector.
cDocIds_arr = [
  ("Coding Style Guide", "14oOr2a0m3IsC1Iugos-C11vcTn5YRbRy3GxOI3qADD4"),
  ("General AI Instructions", "19bDTV5BxnDZ3NM43jXRgSAnH0xvjv0CaW5JGArVbrRI"),
]
cOutPath_obj = Path(__file__).parent / "instructions.md"
cUserLevelPath_obj = Path.home() / ".claude" / "CLAUDE.md"
cUserLevelMarker_str = "General AI Instructions"
cStub_str = "<!-- Standing instructions are already loaded from the user-level CLAUDE.md on this machine. -->\n"
cSeparator_str = "\n\n---\n\n"


# >>>>> fnUserLevelPresent_bol <<<<<
# True when this machine's user-level CLAUDE.md already carries the instructions.
def fnUserLevelPresent_bol():
  if not cUserLevelPath_obj.exists():
    return False
  try:
    return cUserLevelMarker_str in cUserLevelPath_obj.read_text(encoding="utf-8")
  except OSError:
    return False


# >>>>> fnFallbackText_str <<<<<
# Instructions for the agent to fetch the Docs itself, used when the HTTP fetch
# above failed (most likely a blocked-egress sandbox). Lists the Drive file ID
# for each Doc, since the agent needs that to call the Drive connector's read
# tool, not the published URL used for the HTTP route.
def fnFallbackText_str():
  vLines_arr = [
    "<!-- The Coding Style Guide and General AI Instructions could not be fetched",
    "over HTTP in this environment, most likely because outbound access to",
    "docs.google.com is blocked by this sandbox's network policy.",
    "",
    "If the Google Drive connector is available in this session: as your first",
    "action, read the following two Google Docs with it and follow them as your",
    "standing instructions for the rest of this session, in this order.",
    "",
  ]
  for vTitle_str, vId_str in cDocIds_arr:
    vLines_arr.append("- " + vTitle_str + ": file ID " + vId_str)
  vLines_arr += [
    "",
    "If the Drive connector is not available either, proceed without these",
    "instructions, but say so plainly in your final summary rather than silently",
    "working without them. -->",
    "",
  ]
  return "\n".join(vLines_arr)


def fnMain():
  if fnUserLevelPresent_bol():
    conv.fnWriteAtomic(cOutPath_obj, cStub_str)
    return

  vSections_arr = []
  for vTitle_str, vUrl_str in cDocs_arr:
    try:
      vSections_arr.append(conv.fnFetch_str(vUrl_str))
    except Exception as vError_obj:
      print(f"fetch failed for {vTitle_str}: {type(vError_obj).__name__}: {vError_obj}", file=sys.stderr)
      conv.fnWriteAtomic(cOutPath_obj, fnFallbackText_str())
      return

  conv.fnWriteAtomic(cOutPath_obj, cSeparator_str.join(vSections_arr))


if __name__ == "__main__":
  fnMain()
