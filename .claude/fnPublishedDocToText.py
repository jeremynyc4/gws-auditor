"""Turn a Google Doc published to the web into Markdown-flavored plain text.

Google's published HTML carries headings, paragraphs, lists, tables, and
character formatting, but everything is expressed through generated CSS
classes. This module reads those classes back so the output keeps what an
AI reader needs: heading levels, list numbers, bold, italics, links, and
tables. The Google banner around the content ("Published using Google Docs",
"Report abuse", and so on) is dropped.

Usage:
  python "fnPublishedDocToText.py" <published URL> [<output path>]

With an output path the file is written only when the fetch and conversion
both succeed, so a failed fetch leaves the previous file untouched. Without
one the text goes to standard output.
"""

import html.parser
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

cUserAgent_str = "Mozilla/5.0 (fnPublishedDocToText)"
cTimeoutSeconds_int = 60
cContentDivId_str = "contents"
cHeadingTags_arr = ["h1", "h2", "h3", "h4", "h5", "h6"]
cBlockTags_arr = ["p", "li", "td", "th"]
cListTags_arr = ["ol", "ul"]
cTitleHashes_int = 1
cIndentSpaces_int = 2
cBoldMarker_str = "**"
cItalicMarker_str = "*"
cBulletMarker_str = "- "
cListClassPattern_obj = re.compile(r"lst-kix_([a-z0-9_]+)-(\d+)")
cRulePattern_obj = re.compile(r"\.([A-Za-z0-9_-]+)\{([^}]*)\}")


# >>>>> fnClassSets_obj <<<<<
# Reads every stylesheet in the page and returns the class names that make
# text bold or italic, since the body markup never says so directly.
def fnClassSets_obj(pHtml_str):
  vBold_arr = set()
  vItalic_arr = set()

  for vCss_str in re.findall(r"<style[^>]*>(.*?)</style>", pHtml_str, re.S):
    for vMatch_obj in cRulePattern_obj.finditer(vCss_str):
      vClass_str = vMatch_obj.group(1)
      vBody_str = vMatch_obj.group(2)

      if re.search(r"font-weight:\s*(700|bold)", vBody_str):
        vBold_arr.add(vClass_str)

      if re.search(r"font-style:\s*italic", vBody_str):
        vItalic_arr.add(vClass_str)

  return {"bold": vBold_arr, "italic": vItalic_arr}


# >>>>> fnUnwrapLink_str <<<<<
# Google routes every link through google.com/url?q=...; this returns the
# real destination.
def fnUnwrapLink_str(pHref_str):
  vParsed_obj = urllib.parse.urlparse(pHref_str)

  if vParsed_obj.netloc.endswith("google.com") and vParsed_obj.path == "/url":
    vQuery_obj = urllib.parse.parse_qs(vParsed_obj.query)

    if "q" in vQuery_obj:
      return vQuery_obj["q"][0]

  return pHref_str


# >>>>> DocParser <<<<<
# Walks the published HTML once, collecting finished lines. Inline styling
# is applied when a span or link closes, block structure when a block
# element closes. List counters are keyed by Google's list id and level, so
# a list that continues after an interruption keeps counting, while a list
# whose element carries the "start" class starts over.
class DocParser(html.parser.HTMLParser):
  def __init__(self, pClassSets_obj):
    super().__init__(convert_charrefs=True)
    self.vClassSets_obj = pClassSets_obj
    self.vLines_arr = []
    self.vInContents_bol = False
    self.vDivDepth_int = 0
    self.vContentsDepth_int = None
    self.vSkipDepth_int = 0
    self.vSpanStack_arr = []
    self.vLinkStack_arr = []
    self.vBlock_obj = None
    self.vListStack_arr = []
    self.vCounters_obj = {}
    self.vRow_arr = None
    self.vTable_arr = None
    self.vInCell_bol = False

  # ----- helpers -----

  def fnClasses_arr(self, pAttrs_arr):
    vAttrs_obj = dict(pAttrs_arr)
    return (vAttrs_obj.get("class") or "").split()

  def fnOpenBlock(self, pKind_str, pPrefix_str=""):
    self.vBlock_obj = {"kind": pKind_str, "prefix": pPrefix_str, "parts": []}

  def fnCloseBlock(self):
    if self.vBlock_obj is None:
      return

    vText_str = re.sub(r"[ \t]+", " ", "".join(self.vBlock_obj["parts"])).strip()
    vKind_str = self.vBlock_obj["kind"]
    vPrefix_str = self.vBlock_obj["prefix"]
    self.vBlock_obj = None

    if vKind_str == "cell":
      if self.vRow_arr is not None:
        self.vRow_arr.append(vText_str)

      return

    if not vText_str:
      return

    if vKind_str == "heading":
      # A heading is already emphasized by its level, so inline bold or italic
      # markers only add noise, and a forced line break inside it becomes a space.
      vText_str = vText_str.replace(cBoldMarker_str, "").replace(cItalicMarker_str, "")
      vText_str = re.sub(r"\s*\n\s*", " ", vText_str)
      self.vLines_arr.append("")
      self.vLines_arr.append(vPrefix_str + vText_str)
    elif vKind_str == "item":
      self.vLines_arr.append(vPrefix_str + vText_str)
    else:
      self.vLines_arr.append("")
      self.vLines_arr.append(vText_str)

  def fnListPrefix_str(self, pClasses_arr, pAttrs_arr):
    vListId_str = ""
    vLevel_int = 0

    for vClass_str in pClasses_arr:
      vMatch_obj = cListClassPattern_obj.match(vClass_str)

      if vMatch_obj:
        vListId_str = vMatch_obj.group(1)
        vLevel_int = int(vMatch_obj.group(2))

    return vListId_str, vLevel_int

  # ----- tag handling -----

  def handle_starttag(self, pTag_str, pAttrs_arr):
    vClasses_arr = self.fnClasses_arr(pAttrs_arr)
    vAttrs_obj = dict(pAttrs_arr)

    if pTag_str == "div":
      self.vDivDepth_int += 1

      if vAttrs_obj.get("id") == cContentDivId_str:
        self.vInContents_bol = True
        self.vContentsDepth_int = self.vDivDepth_int

      return

    if not self.vInContents_bol:
      return

    if pTag_str in ("script", "style"):
      self.vSkipDepth_int += 1
      return

    # Inside a table cell every block element just separates text within the
    # cell; the cell itself is the block that gets closed.
    if self.vInCell_bol:
      if pTag_str in cBlockTags_arr or pTag_str in cHeadingTags_arr or pTag_str == "br":
        if self.vBlock_obj is not None and self.vBlock_obj["parts"]:
          self.vBlock_obj["parts"].append(" ")

      if pTag_str not in ("span", "a"):
        return

    if pTag_str in cListTags_arr:
      vListId_str, vLevel_int = self.fnListPrefix_str(vClasses_arr, pAttrs_arr)
      vKey_str = f"{vListId_str}-{vLevel_int}"

      if "start" in vClasses_arr or vKey_str not in self.vCounters_obj:
        self.vCounters_obj[vKey_str] = int(vAttrs_obj.get("start", "1")) - 1

      self.vListStack_arr.append({"tag": pTag_str, "key": vKey_str, "level": vLevel_int})
      return

    if pTag_str == "li":
      vList_obj = self.vListStack_arr[-1] if self.vListStack_arr else {"tag": "ul", "key": "", "level": 0}
      vIndent_str = " " * (cIndentSpaces_int * vList_obj["level"])

      if vList_obj["tag"] == "ol":
        self.vCounters_obj[vList_obj["key"]] = self.vCounters_obj.get(vList_obj["key"], 0) + 1
        vPrefix_str = f"{vIndent_str}{self.vCounters_obj[vList_obj['key']]}. "
      else:
        vPrefix_str = vIndent_str + cBulletMarker_str

      self.fnOpenBlock("item", vPrefix_str)
      return

    if pTag_str in cHeadingTags_arr:
      vLevel_int = int(pTag_str[1]) + cTitleHashes_int
      self.fnOpenBlock("heading", "#" * vLevel_int + " ")
      return

    if pTag_str == "p":
      if "title" in vClasses_arr:
        self.fnOpenBlock("heading", "#" * cTitleHashes_int + " ")
      elif "subtitle" in vClasses_arr:
        self.fnOpenBlock("para", "")
      else:
        self.fnOpenBlock("para", "")

      return

    if pTag_str == "table":
      self.vTable_arr = []
      return

    if pTag_str == "tr":
      self.vRow_arr = []
      return

    if pTag_str in ("td", "th"):
      self.vInCell_bol = True
      self.fnOpenBlock("cell", "")
      return

    if pTag_str == "span":
      vBold_bol = any(vClass_str in self.vClassSets_obj["bold"] for vClass_str in vClasses_arr)
      vItalic_bol = any(vClass_str in self.vClassSets_obj["italic"] for vClass_str in vClasses_arr)
      self.vSpanStack_arr.append({"bold": vBold_bol, "italic": vItalic_bol, "start": self.fnPartCount_int()})
      return

    if pTag_str == "a":
      self.vLinkStack_arr.append({"href": fnUnwrapLink_str(vAttrs_obj.get("href", "")), "start": self.fnPartCount_int()})
      return

    if pTag_str == "br" and self.vBlock_obj is not None:
      self.vBlock_obj["parts"].append("\n")

  def fnPartCount_int(self):
    return len(self.vBlock_obj["parts"]) if self.vBlock_obj is not None else 0

  def fnWrapSince(self, pStart_int, pBefore_str, pAfter_str):
    if self.vBlock_obj is None:
      return

    vParts_arr = self.vBlock_obj["parts"]
    vInner_str = "".join(vParts_arr[pStart_int:])
    vLead_str = vInner_str[:len(vInner_str) - len(vInner_str.lstrip())]
    vTrail_str = vInner_str[len(vInner_str.rstrip()):]
    vCore_str = vInner_str.strip()

    if not vCore_str:
      return

    del vParts_arr[pStart_int:]
    vParts_arr.append(vLead_str + pBefore_str + vCore_str + pAfter_str + vTrail_str)

  def handle_endtag(self, pTag_str):
    if pTag_str == "div":
      if self.vInContents_bol and self.vDivDepth_int == self.vContentsDepth_int:
        self.vInContents_bol = False

      self.vDivDepth_int -= 1
      return

    if not self.vInContents_bol:
      return

    if pTag_str in ("script", "style"):
      self.vSkipDepth_int = max(self.vSkipDepth_int - 1, 0)
      return

    if pTag_str == "span" and self.vSpanStack_arr:
      vSpan_obj = self.vSpanStack_arr.pop()

      if vSpan_obj["bold"]:
        self.fnWrapSince(vSpan_obj["start"], cBoldMarker_str, cBoldMarker_str)

      if vSpan_obj["italic"]:
        self.fnWrapSince(vSpan_obj["start"], cItalicMarker_str, cItalicMarker_str)

      return

    if pTag_str == "a" and self.vLinkStack_arr:
      vLink_obj = self.vLinkStack_arr.pop()

      if vLink_obj["href"]:
        self.fnWrapSince(vLink_obj["start"], "[", "](" + vLink_obj["href"] + ")")

      return

    if pTag_str in ("td", "th"):
      self.vInCell_bol = False
      self.fnCloseBlock()
      return

    if self.vInCell_bol:
      return

    if pTag_str in cListTags_arr:
      if self.vListStack_arr:
        self.vListStack_arr.pop()

      return

    if pTag_str in cBlockTags_arr or pTag_str in cHeadingTags_arr:
      self.fnCloseBlock()
      return

    if pTag_str == "tr" and self.vRow_arr is not None and self.vTable_arr is not None:
      self.vTable_arr.append(self.vRow_arr)
      self.vRow_arr = None
      return

    if pTag_str == "table" and self.vTable_arr is not None:
      self.vLines_arr.append("")

      for vIndex_int, vRow_arr in enumerate(self.vTable_arr):
        vCells_arr = [vCell_str.replace("|", "\\|").replace("\n", " ") for vCell_str in vRow_arr]
        self.vLines_arr.append("| " + " | ".join(vCells_arr) + " |")

        if vIndex_int == 0:
          self.vLines_arr.append("|" + "|".join([" --- "] * len(vCells_arr)) + "|")

      self.vTable_arr = None

  def handle_data(self, pData_str):
    if self.vInContents_bol and self.vSkipDepth_int == 0 and self.vBlock_obj is not None:
      self.vBlock_obj["parts"].append(pData_str)


# >>>>> fnConvert_str <<<<<
# Converts published-doc HTML to text. Consecutive blank lines collapse to
# one, and list items sit together with a blank line before the first item.
def fnConvert_str(pHtml_str):
  vParser_obj = DocParser(fnClassSets_obj(pHtml_str))
  vParser_obj.feed(pHtml_str)
  vParser_obj.close()
  vOut_arr = []
  vPrevItem_bol = False

  for vLine_str in vParser_obj.vLines_arr:
    vIsItem_bol = bool(re.match(r"^\s*(\d+\. |- )", vLine_str))

    if vIsItem_bol and not vPrevItem_bol and vOut_arr and vOut_arr[-1] != "":
      vOut_arr.append("")

    if vLine_str == "" and vOut_arr and vOut_arr[-1] == "":
      continue

    vOut_arr.append(vLine_str)
    vPrevItem_bol = vIsItem_bol

  return "\n".join(vOut_arr).strip() + "\n"


# >>>>> fnFetch_str <<<<<
# Downloads the published page and returns it as text.
def fnFetch_str(pUrl_str):
  vRequest_obj = urllib.request.Request(pUrl_str, headers={"User-Agent": cUserAgent_str})

  with urllib.request.urlopen(vRequest_obj, timeout=cTimeoutSeconds_int) as vResponse_obj:
    return fnConvert_str(vResponse_obj.read().decode("utf-8"))


# >>>>> fnWriteAtomic <<<<<
# Writes through a temporary file so a reader never sees a half-written copy.
def fnWriteAtomic(pPath_obj, pText_str):
  vTemp_obj = pPath_obj.with_suffix(pPath_obj.suffix + ".tmp")
  vTemp_obj.write_text(pText_str, encoding="utf-8")
  vTemp_obj.replace(pPath_obj)


def fnMain():
  if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)

  cUrl_str = sys.argv[1]
  cOutPath_obj = Path(sys.argv[2]) if len(sys.argv) > 2 else None

  try:
    vText_str = fnFetch_str(cUrl_str)
  except Exception as vError_obj:
    print(f"FETCH FAILED: {type(vError_obj).__name__}: {vError_obj}", file=sys.stderr)
    sys.exit(1)

  if cOutPath_obj is None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(vText_str, end="")
  else:
    fnWriteAtomic(cOutPath_obj, vText_str)
    print(f"wrote {len(vText_str)} chars to {cOutPath_obj}")


if __name__ == "__main__":
  fnMain()
