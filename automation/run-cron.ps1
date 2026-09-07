# >>>>> run-cron.ps1 <<<<<
# The scheduled wrapper. Runs every N minutes while Jeremy is logged on. It does the
# CHEAP detection itself (GitHub CLI only, no model), and only launches Claude when
# there is real work -- so idle ticks cost essentially nothing, while a trivial
# Claude launch would cost ~$0.12 in loaded context every time.
#
# Flow: take a lock -> run detect-work.py -> if work, launch headless Claude against
# the runbook -> log -> release the lock.
#
# This file is identical across every repo's automation folder except the constants
# block below (Jeremy's rule, 2026-09-06: no divergence between repos). Change behaviour
# here, then copy the file to the other folders and swap only the constants.

$ErrorActionPreference = "Stop"

$cProjectDir  = "C:\Users\Jeremy\Projects\gws-auditor"
$cAutoDir     = Join-Path $cProjectDir "automation"
$cClaudeCmd   = Join-Path $env:APPDATA "npm\claude.cmd"
$cWorkFile    = Join-Path $cAutoDir "work.json"
$cLockFile    = Join-Path $cAutoDir ".cron.lock"
$cLogFile     = Join-Path $cAutoDir "cron.log"
# Resume record for a run the account limit cut short mid-Issue (added 2026-09-07 at
# Jeremy's request, option 3 of the throttled-session discussion). Holds the session id,
# the Issue, and the reset time; see fnWriteResume and the launch block for the flow.
$cResumeFile  = Join-Path $cAutoDir "resume.json"
$cResumeMaxDays_int = 7                   # drop a resume record older than this, whatever its state
$cResumeGraceMin_int = 2                  # launch this long after the stated reset, not on the dot
$cLimitPattern_str = 'session limit|usage limit|hit your limit|rate limit'
$cRunbook     = "automation/claude-cron-runbook.md"   # pointer to the Doc, for humans
$cRunbookFetched = "automation/runbook.fetched.md"   # generated text copy the run reads
# The runbook is a Google Doc published to the web; each run that has work fetches
# the published copy into $cRunbookFetched first, so edits in the Doc take effect at
# the next tick. The converter keeps headings, list numbers, bold, and links.
$cRunbookUrl  = "https://docs.google.com/document/d/e/2PACX-1vTxMEI1ruta5ZRahK8Kr_Dy_nni-y7XO6Mh7gzbMiEUgOphtABwHQEnTNXVSGp0OgZgTL9A-TxQY3kj/pub"
$cConverter   = Join-Path $env:USERPROFILE ".claude\fnPublishedDocToText.py"
# No per-run spend flag. Removed 2026-09-05 at Jeremy's instruction: a run works its item
# to completion; the cost is bounded by the work, not by a guess made in advance.
$cLockMaxMin  = 55                        # backstop: clear even a live holder's lock past this age
$cMaxLaunchesPerTick = 6                  # most Claude launches one tick may make
$cBotLogin    = "claudebot-ymerej"        # the account Claude posts as
$cRepo         = "jeremynyc4/gws-auditor"
$cProjectOwner = "jeremynyc4"
$cProjectNum   = "5"
$cWarnDays     = 21                       # warn this many days before the bot token expires
$cShowWindow   = $false                   # $true = visible labelled window while Claude works
# Extra flags for the Claude launch. "--chrome" attaches the Claude in Chrome extension in
# Jeremy's logged-on browser (added 2026-09-04, Issues #2 and #19, for Roll20); leave it
# empty in a repo whose work never needs a browser.
$cLaunchFlags  = ""
# Cron default launch profile: SH (Sonnet, high effort), per Jeremy's 2026-09-06 switch.
# An item can override this for itself with a "fh" or "sh" label (detect-work.py reads
# the label and reports it as that item's "profile"); this is what a launch falls back to
# when the item handed to this run carries neither label.
$cDefaultModelArgs_str = "--model sonnet --effort high"
$cProfileModelArgs_obj = @{
  "fh" = "--model fable --effort high"
  "sh" = "--model sonnet --effort high"
}

# >>>>> fnModelArgsForProfile_str <<<<<
# Resolves a Do Now item's "profile" (from its "fh"/"sh" board label, or $null/empty if it
# carries neither) to the --model/--effort flags for its launch. Unrecognized values fall
# back to the cron default rather than failing the run.
function fnModelArgsForProfile_str($pProfile_str) {
  if ($pProfile_str -and $cProfileModelArgs_obj.ContainsKey($pProfile_str)) {
    return $cProfileModelArgs_obj[$pProfile_str]
  }
  return $cDefaultModelArgs_str
}

# A benign stderr line every headless run prints, harmless but alarming to read. The
# SessionEnd/Stop hook (ai-conversation-adapter.py, saves the MLC transcript) always
# completes successfully in its own debug log even when this prints -- Claude Code's
# headless mode reports the hook "cancelled" because it exits without waiting to see
# whether the hook finished, not because the hook actually failed. Verified 2026-09-06 by
# cross-checking every cron run's timestamp against ai-adapter-debug.txt in Jeremy's
# .claude folder, which shows the hook completing in about a second every time. Filtered
# out of the log rather than out of last-run.err itself, so that file stays the untouched
# raw record.
$cBenignStderr_arr = @(
  'SessionEnd hook \[python3 "C:/Users/Jeremy/\.claude/ai-conversation-adapter\.py"\] failed: Hook cancelled',
  'Stop hook \[python3 "C:/Users/Jeremy/\.claude/ai-conversation-adapter\.py"\] failed: Hook cancelled'
)

function Write-Log($msg) {
  $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  Add-Content -Path $cLogFile -Value "$stamp  $msg" -Encoding utf8
}

# >>>>> fnRaiseAlert <<<<<
# Surfaces a problem as a GitHub issue on the board, assigned to Jeremy, instead of
# leaving it to die in cron.log where nobody will ever read it. Deduped on the exact
# title, so a condition that persists for months does not open an issue every five
# minutes. Failure to raise the alert is itself logged and never aborts the run.
function fnRaiseAlert($pTitle_str, $pBody_str) {
  try {
    # Dedup on title across ALL issues, not just open ones: Open/Closed is not a signal on
    # this board (every issue is kept Open; Status is the single source of truth), so
    # "already raised" means an issue of this title exists at all, regardless of state.
    $vTitles_str = (& gh issue list --repo $cRepo --state all --limit 500 --json title --jq '.[].title' | Out-String)
    if ($vTitles_str -split "`n" | Where-Object { $_.Trim() -eq $pTitle_str }) {
      return   # already raised
    }
    # Every Issue the bot creates is assigned to the bot by default (Jeremy's rule,
    # 2026-09-05); Jeremy is added here because an alert needs an action from him.
    $vUrl_str = (& gh issue create --repo $cRepo --title $pTitle_str --body $pBody_str --assignee $cBotLogin --assignee jeremynyc4 | Out-String).Trim()
    $vUrl_str = ($vUrl_str -split "`n" | Where-Object { $_ -match '^https://' } | Select-Object -Last 1)
    Write-Log "ALERT raised as a GitHub issue: $pTitle_str  $vUrl_str"
    # Issues are not added to the board automatically, and the board is where Jeremy
    # actually looks -- an alert only in the repo would be missed.
    if ($vUrl_str) {
      try {
        & gh project item-add $cProjectNum --owner $cProjectOwner --url $vUrl_str | Out-Null
        Write-Log "  added alert to project #$cProjectNum"
      } catch {
        Write-Log "  could not add alert to the board: $($_.Exception.Message)"
      }
    }
  } catch {
    Write-Log "could not raise alert issue ('$pTitle_str'): $($_.Exception.Message)"
  }
}

# >>>>> fnBotTokenDaysLeft <<<<<
# Returns the number of whole days until the current GH_TOKEN expires, or -1 if GitHub
# reports no expiry date. GitHub returns this on every API response as the header
# "Github-Authentication-Token-Expiration", formatted like "2027-07-20 04:00:00 UTC" --
# which no built-in parser handles, hence the explicit format below.
function fnBotTokenDaysLeft {
  $cFormat_str = 'yyyy-MM-dd HH:mm:ss'
  try {
    $vHeader_str = (& gh api -i user | Select-String -Pattern '^Github-Authentication-Token-Expiration:' | Out-String).Trim()
    if (-not $vHeader_str) { return -1 }
    $vRaw_str = ($vHeader_str -split ':\s*', 2)[1].Trim()
    $vClean_str = ($vRaw_str -replace '\s*UTC\s*$', '').Trim()
    $vExpires_dte = [datetime]::ParseExact($vClean_str, $cFormat_str, [Globalization.CultureInfo]::InvariantCulture)
    return [math]::Floor((New-TimeSpan -Start ([datetime]::UtcNow) -End $vExpires_dte).TotalDays)
  } catch {
    return -1
  }
}

# >>>>> fnWriteLock <<<<<
# Writes the lock as two lines: the time, and this wrapper's own process id. The id lets
# the next tick tell a wrapper that is still working from one that was killed from
# outside (2026-09-07: another session force-stopped every wscript.exe on the machine
# 100 seconds into a run; the orphaned run finished, but the lock sat for 55 minutes).
function fnWriteLock {
  $vLines_arr = @((Get-Date).ToString("s"), $PID)
  Set-Content -Path $cLockFile -Value $vLines_arr -Encoding utf8
}

# >>>>> fnLockHolderState_str <<<<<
# Reads the process id on the lock's second line and reports "alive" when a powershell
# process with that id still exists, "dead" when none does, and "unknown" when the lock
# predates the id line. The name check guards against the id being reused by an
# unrelated process after the wrapper died.
function fnLockHolderState_str {
  $cWrapperProcessName_str = "powershell"
  $vLines_arr = @(Get-Content -Path $cLockFile -ErrorAction SilentlyContinue)
  $vHolderPid_int = 0
  if ($vLines_arr.Count -lt 2) { return "unknown" }
  if (-not [int]::TryParse($vLines_arr[1].Trim(), [ref]$vHolderPid_int)) { return "unknown" }
  $vProcess_obj = Get-Process -Id $vHolderPid_int -ErrorAction SilentlyContinue
  if ($vProcess_obj -and $vProcess_obj.ProcessName -eq $cWrapperProcessName_str) { return "alive" }
  return "dead"
}

# >>>>> fnParseResetTime_dte <<<<<
# Reads the reset time out of the account-limit message Claude returns, such as
# "You've hit your session limit - resets 3:40am (America/New_York)", and returns it as
# the next local date-time at that clock time, or $null when the message carries no time
# this function recognises. The zone in the message is taken to be the laptop's own zone;
# Jeremy's machine and account both sit in New York. A message giving a date as well
# ("resets Sep 12, 4am" is the guessed shape for the weekly limit; confidence low) is
# tried second. No match means the caller retries every tick, which is what happened
# before this function existed, so a miss costs nothing new.
function fnParseResetTime_dte($pMessage_str) {
  $cTimeOnly_str = 'resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)'
  $cDateAndTime_str = 'resets\s+([A-Za-z]{3,9}\s+\d{1,2})(?:,?\s*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm))?'
  $cHoursPerHalfDay_int = 12
  $vNow_dte = Get-Date
  $vMatch_obj = [regex]::Match($pMessage_str, $cTimeOnly_str, 'IgnoreCase')
  if ($vMatch_obj.Success) {
    $vHour_int = [int]$vMatch_obj.Groups[1].Value
    $vMinute_int = 0
    if ($vMatch_obj.Groups[2].Success) { $vMinute_int = [int]$vMatch_obj.Groups[2].Value }
    $vHalf_str = $vMatch_obj.Groups[3].Value.ToLower()
    if ($vHalf_str -eq 'pm' -and $vHour_int -lt $cHoursPerHalfDay_int) { $vHour_int += $cHoursPerHalfDay_int }
    if ($vHalf_str -eq 'am' -and $vHour_int -eq $cHoursPerHalfDay_int) { $vHour_int = 0 }
    $vReset_dte = $vNow_dte.Date.AddHours($vHour_int).AddMinutes($vMinute_int)
    if ($vReset_dte -le $vNow_dte) { $vReset_dte = $vReset_dte.AddDays(1) }
    return $vReset_dte
  }
  $vMatch_obj = [regex]::Match($pMessage_str, $cDateAndTime_str, 'IgnoreCase')
  if ($vMatch_obj.Success) {
    try {
      $vDate_dte = [datetime]::Parse($vMatch_obj.Groups[1].Value + " " + $vNow_dte.Year, [Globalization.CultureInfo]::InvariantCulture)
      if ($vMatch_obj.Groups[2].Success) {
        $vHour_int = [int]$vMatch_obj.Groups[2].Value
        $vMinute_int = 0
        if ($vMatch_obj.Groups[3].Success) { $vMinute_int = [int]$vMatch_obj.Groups[3].Value }
        $vHalf_str = $vMatch_obj.Groups[4].Value.ToLower()
        if ($vHalf_str -eq 'pm' -and $vHour_int -lt $cHoursPerHalfDay_int) { $vHour_int += $cHoursPerHalfDay_int }
        if ($vHalf_str -eq 'am' -and $vHour_int -eq $cHoursPerHalfDay_int) { $vHour_int = 0 }
        $vDate_dte = $vDate_dte.Date.AddHours($vHour_int).AddMinutes($vMinute_int)
      }
      # A date earlier than today with no year given belongs to next year (a January
      # reset read in December).
      if ($vDate_dte -lt $vNow_dte.Date) { $vDate_dte = $vDate_dte.AddYears(1) }
      return $vDate_dte
    } catch {
      return $null
    }
  }
  return $null
}

# >>>>> fnReadResume_obj <<<<<
# Returns the pending resume record, or $null when there is none. A record that cannot
# be parsed, or that is older than $cResumeMaxDays_int, is removed and logged rather
# than left to trip every future tick.
function fnReadResume_obj {
  if (-not (Test-Path $cResumeFile)) { return $null }
  $vRecord_obj = $null
  try {
    $vRecord_obj = Get-Content -Path $cResumeFile -Raw -Encoding utf8 | ConvertFrom-Json
  } catch {
    Write-Log "resume record was not readable; removing it: $($_.Exception.Message)"
    Remove-Item -Path $cResumeFile -ErrorAction SilentlyContinue
    return $null
  }
  if (-not $vRecord_obj.session_id -or -not $vRecord_obj.issue) {
    Write-Log "resume record lacked a session id or Issue number; removing it"
    Remove-Item -Path $cResumeFile -ErrorAction SilentlyContinue
    return $null
  }
  $vAgeDays_flt = (New-TimeSpan -Start ([datetime]$vRecord_obj.recorded_at) -End (Get-Date)).TotalDays
  if ($vAgeDays_flt -gt $cResumeMaxDays_int) {
    Write-Log "resume record for #$($vRecord_obj.issue) is $([math]::Round($vAgeDays_flt,1)) days old; dropping it, the Issue starts fresh"
    Remove-Item -Path $cResumeFile -ErrorAction SilentlyContinue
    return $null
  }
  return $vRecord_obj
}

# >>>>> fnWriteResume <<<<<
# Records the session a limit-hit run left behind, so the first tick after the reset can
# continue it with --resume instead of starting the Issue over. resets_at is empty when
# the message's reset time could not be read; the launch block then retries each tick.
function fnWriteResume($pSessionId_str, $pIssue_int, $pMessage_str) {
  $vReset_dte = fnParseResetTime_dte $pMessage_str
  $vResetsAt_str = ""
  if ($vReset_dte) { $vResetsAt_str = $vReset_dte.AddMinutes($cResumeGraceMin_int).ToString("s") }
  $vRecord_obj = [ordered]@{
    session_id  = $pSessionId_str
    issue       = $pIssue_int
    resets_at   = $vResetsAt_str
    recorded_at = (Get-Date).ToString("s")
    message     = $pMessage_str
  }
  # WriteAllText with a BOM-less encoding: Set-Content -Encoding utf8 writes a BOM, which
  # ConvertFrom-Json on the read side does not always tolerate (2026-09-07 lesson).
  $vJson_str = ($vRecord_obj | ConvertTo-Json -Compress)
  [System.IO.File]::WriteAllText($cResumeFile, $vJson_str, (New-Object System.Text.UTF8Encoding($false)))
  if ($vResetsAt_str) {
    Write-Log "  resume record written for #$pIssue_int (session $pSessionId_str); nothing launches before $vResetsAt_str"
  } else {
    Write-Log "  resume record written for #$pIssue_int (session $pSessionId_str); reset time not read from the message, so every tick will try"
  }
}

# --- lock: never run two at once; clear a lock whose wrapper is gone or too old ---
if (Test-Path $cLockFile) {
  $age = (New-TimeSpan -Start (Get-Item $cLockFile).LastWriteTime -End (Get-Date)).TotalMinutes
  $vHolderState_str = fnLockHolderState_str
  if ($vHolderState_str -eq "dead") {
    Write-Log "clearing lock left by a wrapper that is no longer running (age $([math]::Round($age,1))m)"
  } elseif ($age -lt $cLockMaxMin) {
    Write-Log "skip: another run holds the lock (age $([math]::Round($age,1))m, holder $vHolderState_str)"
    return
  } else {
    Write-Log "clearing stale lock (age $([math]::Round($age,1))m, holder $vHolderState_str)"
  }
}
fnWriteLock

try {
  Set-Location $cProjectDir

  # --- act as the bot account so Claude's comments are distinguishable from Jeremy's ---
  # GH_TOKEN takes precedence over gh's stored keyring credentials for every gh call in
  # this process and in the Claude window launched below, so setting it here is enough.
  # A missing or dead token falls back to Jeremy's own credentials rather than failing the
  # run: the work still gets done, it is just attributed to him, and the log says so. The
  # token is validated before use, because a silently-wrong identity would quietly corrupt
  # the comment-authorship signal that mention dedup now depends on.
  $botToken = [Environment]::GetEnvironmentVariable('GH_TOKEN_BOT', 'User')
  if ($botToken) {
    $env:GH_TOKEN = $botToken
    $whoAmI = ""
    $vAuthErr_str = ""
    try {
      $whoAmI = (& gh api user --jq '.login' 2>"$env:TEMP\claude-cron-auth.err" | Out-String).Trim()
      if (Test-Path "$env:TEMP\claude-cron-auth.err") { $vAuthErr_str = (Get-Content "$env:TEMP\claude-cron-auth.err" -Raw) }
    } catch { $whoAmI = "" }
    # No network is not a dead token. GitHub unreachable means nothing can be checked or
    # worked this tick, so skip it quietly rather than raise a false token alert.
    if (-not $whoAmI -and $vAuthErr_str -match 'internet connection|githubstatus|dial tcp|no such host|TLS handshake|timeout') {
      Write-Log "offline: GitHub unreachable, skipping this tick"
      return
    }
    if ($whoAmI -eq $cBotLogin) {
      Write-Log "authenticated as $cBotLogin"

      # Watch the credential's own expiry. Without this the token simply dies one day and
      # the cron quietly reverts to posting as Jeremy -- working, but with the authorship
      # signal silently gone, which is exactly the kind of failure nobody notices for months.
      $daysLeft = fnBotTokenDaysLeft
      if ($daysLeft -ge 0 -and $daysLeft -le $cWarnDays) {
        Write-Log "WARNING: bot token expires in $daysLeft day(s)"
        fnRaiseAlert "Bot token for claudebot-ymerej expires soon" @"
The GitHub personal access token the Claude cron posts with expires in **$daysLeft day(s)**.

When it expires nothing breaks loudly -- the cron falls back to posting as ``jeremynyc4``,
so comments silently stop being attributable to the bot and mention-dedup drops back to the
hidden-marker fallback.

**To rotate it:** sign into GitHub as ``claudebot-ymerej`` **in an incognito window** (doing
this from your normal browser creates a token for your own account instead), create a token
at https://github.com/settings/tokens/new with scopes ``repo``, ``project``, ``read:org``,
then run:

``````
powershell -ExecutionPolicy Bypass -File "$cAutoDir\fnSetBotToken.ps1"
``````

Close this issue once the new token is in place. It will not be raised again while it is open.
"@
      }
    } else {
      $env:GH_TOKEN = $null
      Write-Log "WARNING: GH_TOKEN_BOT did not authenticate as $cBotLogin (got '$whoAmI'). Falling back to Jeremy's credentials; comment authorship will be ambiguous this run."
      fnRaiseAlert "Bot token for claudebot-ymerej is no longer working" @"
The Claude cron tried to authenticate with ``GH_TOKEN_BOT`` and GitHub did not return
``claudebot-ymerej`` (it returned '``$whoAmI``'). The token has most likely expired or been revoked.

The cron is still running, but posting as ``jeremynyc4`` -- so its comments are no longer
distinguishable from yours, and mention-dedup is relying on the hidden-marker fallback.

**To fix:** create a fresh token while signed in as ``claudebot-ymerej`` in an incognito
window (scopes ``repo``, ``project``, ``read:org``), then run:

``````
powershell -ExecutionPolicy Bypass -File "$cAutoDir\fnSetBotToken.ps1"
``````

Close this issue once it is sorted. It will not be raised again while it is open.
"@
    }
  } else {
    Write-Log "WARNING: GH_TOKEN_BOT is not set. Running as Jeremy; comment authorship will be ambiguous."
  }

  # --- work the queue, one item at a time, until it is empty ---
  # Jeremy's rule (2026-09-05): the board is the queue. A tick works one Do Now item at a
  # time and, when that item is Submitted or Paused, moves straight on to the next one
  # without waiting for the next tick. The detector hands over only the first Do Now item
  # in board order; the run itself continues to the next item when done (runbook Part 1),
  # and this loop re-detects after every run so a run cut short by a crash or an early
  # exit does not stall the queue until the next tick.
  #
  # Guards against spinning: a per-tick launch cap, and a stop if the same item comes back
  # as the first Do Now item two launches in a row (it was handed back rather than
  # advanced, and a third try would not go differently).
  $setStatus = Join-Path $cAutoDir "set-status.py"
  $vLaunches_int = 0
  $vPrevFirst_int = -1

  while ($true) {
    # --- cheap detection ---
    $detectOut = & python (Join-Path $cAutoDir "detect-work.py") 2>&1 | Out-String
    Set-Content -Path $cWorkFile -Value $detectOut -Encoding utf8

    $work = $null
    try { $work = $detectOut | ConvertFrom-Json } catch {
      Write-Log "detector produced non-JSON output; stopping this tick. Raw: $($detectOut.Trim())"
      break
    }
    if (-not $work.ok) {
      Write-Log "detector error: $($work.error)"
      break
    }
    $vUnassigned_arr = @($work.do_now_unassigned | Where-Object { $_ })
    $vUnassignedNote_str = ""
    if ($vUnassigned_arr.Count -gt 0) {
      $vUnassignedNote_str = "; left alone " + $vUnassigned_arr.Count + " Do Now item(s) not assigned to $cBotLogin (#" + ($vUnassigned_arr -join ", #") + ")"
    }
    if (-not $work.has_work) {
      if ($vLaunches_int -eq 0) { Write-Log "idle: nothing in Do Now, no new @claude mentions$vUnassignedNote_str" }
      else { Write-Log "queue drained after $vLaunches_int launch(es)$vUnassignedNote_str" }
      break
    }
    if ($vLaunches_int -ge $cMaxLaunchesPerTick) {
      Write-Log "launch cap ($cMaxLaunchesPerTick) reached this tick; the rest of the queue waits for the next tick"
      break
    }

    $doNowNums = @($work.do_now | ForEach-Object { $_.number } | Where-Object { $_ })
    $queueNums = @($work.do_now_queue | Where-Object { $_ })
    $vFirst_int = -1
    if ($doNowNums.Count -gt 0) { $vFirst_int = [int]$doNowNums[0] }
    if ($vFirst_int -ge 0 -and $vFirst_int -eq $vPrevFirst_int) {
      Write-Log "#$vFirst_int is back in Do Now after its own run; stopping this tick so it does not loop"
      break
    }
    $vPrevFirst_int = $vFirst_int

    # --- a run the account limit cut short: wait for the reset, then resume its session ---
    # The reconcile step below hands a cut-short Issue back to Do Now and, when the run
    # had already taken it on, records the run's session in $cResumeFile. Until the reset
    # time in that record nothing launches (the limit is account-wide, so any launch would
    # be turned away). After it, if the recorded Issue is the one about to be worked, the
    # launch continues that session with --resume instead of starting the Issue over. A
    # record whose Issue has left the bot's Do Now queue (Jeremy moved it, or another run
    # finished it) is dropped. A record whose Issue is still queued but not first waits
    # its turn; the fresh run ahead of it may reach the Issue on its own, in which case
    # the record drops on the tick after.
    $vResume_obj = fnReadResume_obj
    $vResumeArgs_str = ""
    $vResumeIssue_int = -1
    if ($vResume_obj) {
      $vResumeIssue_int = [int]$vResume_obj.issue
      if (-not ($queueNums -contains $vResumeIssue_int)) {
        Write-Log "resume record for #$vResumeIssue_int dropped: the Issue is no longer in the bot's Do Now queue"
        Remove-Item -Path $cResumeFile -ErrorAction SilentlyContinue
        $vResumeIssue_int = -1
      } elseif ($vResume_obj.resets_at -and ((Get-Date) -lt [datetime]$vResume_obj.resets_at)) {
        Write-Log "waiting: the account limit that cut short #$vResumeIssue_int resets at $($vResume_obj.resets_at); nothing launched this tick"
        break
      } elseif ($vFirst_int -eq $vResumeIssue_int) {
        $vResumeArgs_str = "--resume $($vResume_obj.session_id)"
        Write-Log "resuming session $($vResume_obj.session_id) for #$vResumeIssue_int (cut short $($vResume_obj.recorded_at))"
      }
    }

    $nDoNow = $doNowNums.Count
    $nWait  = [int]$work.do_now_waiting
    $nMent  = @($work.mentions).Count
    $nNewC  = @($work.new_comments).Count
    Write-Log "work found: $nDoNow Do Now ($nWait more waiting), $nMent mention(s), $nNewC issue(s) with new comments to read -> launching Claude$vUnassignedNote_str"

    # --- resolve this run's launch profile from the item being handed to it ---
    # Only the first Do Now item (the one about to be worked) can set the profile; a
    # mention-only tick launches at the cron default. detect-work.py already read the
    # item's "fh"/"sh" board label and resolved it to a "profile" of "fh", "sh", or $null
    # (no label, or both labels at once -- see do_now_label_conflicts below).
    $vProfile_str = $null
    if (@($work.do_now).Count -gt 0) { $vProfile_str = $work.do_now[0].profile }
    $vModelArgs_str = fnModelArgsForProfile_str $vProfile_str
    if ($vProfile_str) {
      Write-Log "  launch profile: $vProfile_str (label override) -> $vModelArgs_str"
    } else {
      Write-Log "  launch profile: cron default -> $vModelArgs_str"
    }
    $vConflicts_arr = @($work.do_now_label_conflicts | Where-Object { $_ })
    if ($vConflicts_arr.Count -gt 0) {
      Write-Log "  WARNING: Do Now item(s) carry both fh and sh labels, ignored (default used): #$($vConflicts_arr -join ', #')"
    }

    # --- refresh the runbook from the published Google Doc, once per tick ---
    # The converter writes the file only when the fetch succeeds, so a failed fetch
    # leaves the previous copy in place and the run proceeds on that. With no previous
    # copy there is no runbook at all, so the tick ends before anything is touched.
    $vRunbookPath_str = Join-Path $cProjectDir $cRunbookFetched
    if ($vLaunches_int -eq 0) {
      $vFetchOut_str = (& python $cConverter $cRunbookUrl $vRunbookPath_str 2>&1 | Out-String).Trim()
      if ($LASTEXITCODE -eq 0) {
        Write-Log "runbook refreshed from the published Doc ($vFetchOut_str)"
      } elseif (Test-Path $vRunbookPath_str) {
        Write-Log "runbook fetch FAILED, using the previous fetched copy: $vFetchOut_str"
      } else {
        Write-Log "runbook fetch FAILED and no fetched copy exists; skipping this tick: $vFetchOut_str"
        break
      }
    }

    # --- hand the work to the run WITHOUT setting In Progress ---
    # Jeremy's rule (2026-09-06): In Progress means the work has been taken on. The run sets
    # it as its own first action, for Do Now items and mentions alike, so a run that cannot
    # work at all (account limit, no tokens, offline) never marks anything. Overnight
    # 2026-09-05 the wrapper pre-set eight items In Progress for runs that each returned the
    # account-limit message instantly. For a mention the run also chooses the closing
    # status: the prior status is in work.json for it to restore when it only answered.
    foreach ($n in $doNowNums) {
      Write-Log "  handing Do Now #$n to the run"
    }
    foreach ($m in @($work.mentions)) {
      if ($m.issue -and -not ($doNowNums -contains $m.issue)) {
        Write-Log "  handing mention on #$($m.issue) to the run (status '$($m.status)')"
      }
    }

    # Keep the lock fresh, so a long tick working several items is not taken for a
    # stale one by the next scheduled start.
    fnWriteLock

    # --- do the work ---
    # Runs with no console window at all by default ($cShowWindow = $false), because a
    # window popping up and stealing keyboard focus mid-task is disruptive. The earlier
    # visible-window design existed to stop the window being closed by accident; with no
    # window there is nothing to close, so that concern is moot. The trade-off is that a
    # run is now invisible while it happens -- cron.log is the place to see what happened.
    # Set $cShowWindow to $true to get the old labelled, visible window back.
    #
    # A small launcher .cmd runs headless Claude with its JSON output redirected to a file
    # so the log can still be built.
    #
    # $cLaunchFlags carries any per-repo launch flags, such as --chrome for browser work.
    $prompt = "You are an unattended scheduled run. Read automation/runbook.fetched.md and follow it exactly. The work items detected for this run are in automation/work.json. Your first action on a Do Now item or a mentioned Issue is to set it to In Progress with automation/set-status.py; if you cannot do the work (account limit, no tokens, offline), set nothing to In Progress and exit. Read every Issue listed under new_comments before you start. Act only within the runbook's constraints, then exit."
    # A resumed launch continues the cut-short session, so it already holds the runbook
    # and its own progress; the prompt tells it what changed since: the reset has passed,
    # the wrapper handed the Issue back to Do Now, and the board may have moved on.
    if ($vResumeArgs_str) {
      $prompt = "You are the same unattended scheduled run that the account's usage limit cut short; the limit has now reset. Continue the work on Issue #$vResumeIssue_int from where you left off. Before acting, re-read automation/runbook.fetched.md and automation/work.json and check the Issue's current status and latest comments on GitHub: the wrapper handed it back to Do Now, so set it to In Progress again with automation/set-status.py. Do not redo steps that are already committed or commented. Finish the Issue and close it out per the runbook, then exit."
    }

    $cResultFile = Join-Path $cAutoDir "last-run.json"
    $cErrFile    = Join-Path $cAutoDir "last-run.err"
    $cLauncher   = Join-Path $cAutoDir "run-claude.cmd"
    Remove-Item $cResultFile -ErrorAction SilentlyContinue

    if ($cShowWindow) {
      $launcherBody = @"
@echo off
title   ***  CLAUDE IS WORKING - DO NOT CLOSE THIS WINDOW  ***
echo ==============================================================
echo.
echo     CLAUDE AUTOMATION IS RUNNING - PLEASE DO NOT CLOSE.
echo.
echo     This window closes itself when the task finishes.
echo     Closing it early only interrupts the work in progress.
echo.
echo ==============================================================
echo.
call "$cClaudeCmd" -p "$prompt" $vResumeArgs_str $vModelArgs_str --permission-mode bypassPermissions $cLaunchFlags --add-dir "$cProjectDir" --output-format json < nul > "$cResultFile" 2> "$cErrFile"
"@
    } else {
      $launcherBody = @"
@echo off
call "$cClaudeCmd" -p "$prompt" $vResumeArgs_str $vModelArgs_str --permission-mode bypassPermissions $cLaunchFlags --add-dir "$cProjectDir" --output-format json < nul > "$cResultFile" 2> "$cErrFile"
"@
    }
    Set-Content -Path $cLauncher -Value $launcherBody -Encoding ascii

    if ($cShowWindow) {
      # Visible window (default Normal style); -Wait holds the lock for the whole run.
      Start-Process -FilePath "cmd.exe" -ArgumentList '/c', "`"$cLauncher`"" -Wait
    } else {
      # CreateNoWindow via ProcessStartInfo, rather than Start-Process -WindowStyle Hidden:
      # the latter still creates a console that can flash and grab focus before it hides.
      # UseShellExecute must be false for CreateNoWindow to be honoured. Redirection is
      # handled inside the .cmd, so no stream plumbing is needed here.
      $vStartInfo_obj = New-Object System.Diagnostics.ProcessStartInfo
      $vStartInfo_obj.FileName        = "cmd.exe"
      $vStartInfo_obj.Arguments       = "/c `"$cLauncher`""
      $vStartInfo_obj.UseShellExecute = $false
      $vStartInfo_obj.CreateNoWindow  = $true
      $vStartInfo_obj.WorkingDirectory = $cProjectDir
      $vProcess_obj = [System.Diagnostics.Process]::Start($vStartInfo_obj)
      $vProcess_obj.WaitForExit()
    }
    $vLaunches_int++

    $result = if (Test-Path $cResultFile) { Get-Content $cResultFile -Raw } else { "" }

    # Surface anything on stderr that isn't the known-benign hook-report line, so a real
    # error doesn't sit unread in last-run.err. Silence when only the benign line is there.
    if (Test-Path $cErrFile) {
      $vErrLines_arr = @(Get-Content $cErrFile | Where-Object { $_.Trim() } | Where-Object {
        $vLine_str = $_
        -not ($cBenignStderr_arr | Where-Object { $vLine_str -match $_ })
      })
      if ($vErrLines_arr.Count -gt 0) {
        Write-Log "  run stderr: $($vErrLines_arr -join ' | ')"
      }
    }

    # pull the cost + short result out of the JSON envelope for the log
    $vRunBlocked_bol = $false
    $vLimitHit_bol = $false
    $vSessionId_str = ""
    $vLimitMessage_str = ""
    try {
      $r = $result | ConvertFrom-Json
      $cost = [math]::Round([double]$r.total_cost_usd, 3)
      $summary = ($r.result -replace "\s+", " ")
      if ($summary.Length -gt 300) { $summary = $summary.Substring(0,300) + "..." }
      Write-Log "run complete: cost $cost USD | $summary"
      $vSessionId_str = "$($r.session_id)"
      # A run that errored, or that the account's session or usage limit turned away, did
      # no work and the next one would not either. Reconcile, then end the tick. The
      # untruncated result is kept for the limit case, as the reset time sits in it.
      if ($summary -match $cLimitPattern_str) {
        $vLimitHit_bol = $true
        $vLimitMessage_str = ("$($r.result)" -replace "\s+", " ")
      }
      if ($r.is_error -eq $true -or $vLimitHit_bol) {
        $vRunBlocked_bol = $true
      }
    } catch {
      Write-Log "run finished but output was not parseable JSON. Raw tail: $($result.Substring([math]::Max(0,$result.Length-300)))"
      $vRunBlocked_bol = $true
    }

    # --- reconcile statuses ---
    # Any item that was in Do Now when this launch started and is In Progress now was picked
    # up by the run (it sets In Progress itself) and never reached Submitted or Paused: the
    # run was cut short. Hand it back to Do Now so the next launch
    # retries it. One board read covers the whole queue. The queue holds only items assigned
    # to the bot (the detector filters on assignment), so an item Jeremy set to In Progress
    # himself is touched only if it is the bot's and was in Do Now when the run launched.
    # Mention issues are the run's to close out; the wrapper no longer touches them.
    $vHandedBack_arr = @()
    if ($queueNums.Count -gt 0) {
      try {
        # One lean board read (1 GraphQL point) through set-status.py --list, which prints
        # "number<TAB>status" per item. Asking Python avoids passing a GraphQL query through
        # PowerShell's native-argument quoting, which silently broke this step before.
        $vLines_arr = @((& python $setStatus "--list" 2>&1 | Out-String) -split "`n")
        foreach ($vLine_str in $vLines_arr) {
          $vParts_arr = $vLine_str.Trim() -split "`t"
          if ($vParts_arr.Count -lt 2) { continue }
          $n = 0
          if (-not [int]::TryParse($vParts_arr[0], [ref]$n)) { continue }
          if (($queueNums -contains $n) -and ($vParts_arr[1].Trim() -eq "In Progress")) {
            $null = & python $setStatus $n "Do Now" "In Progress" 2>&1
            Write-Log "  #$n left In Progress by a cut-short run -> back to Do Now"
            $vHandedBack_arr += $n
          }
        }
      } catch {
        Write-Log "  could not read the board to reconcile the queue: $($_.Exception.Message)"
      }
    }

    # --- remember or forget the session, for the resume path above ---
    # A limit-hit run that had taken an Issue on (it was handed back just now) is worth
    # continuing, so its session is recorded. A limit-hit run that took nothing on had done
    # no work, so there is nothing to resume. A resumed launch that ended any other way,
    # finished or failed, is over either way, so its record goes.
    if ($vLimitHit_bol -and $vHandedBack_arr.Count -gt 0 -and $vSessionId_str) {
      fnWriteResume $vSessionId_str ([int]$vHandedBack_arr[0]) $vLimitMessage_str
    } elseif ($vResumeArgs_str) {
      Remove-Item -Path $cResumeFile -ErrorAction SilentlyContinue
      if ($vRunBlocked_bol) {
        Write-Log "  resumed run for #$vResumeIssue_int did not complete; record dropped, the Issue starts fresh next time"
      } else {
        Write-Log "  resumed run for #$vResumeIssue_int finished; record dropped"
      }
    }
    if ($vRunBlocked_bol) {
      Write-Log "run did no work (error or account limit); ending this tick, the queue waits"
      break
    }
  }
}
catch {
  Write-Log "ERROR: $($_.Exception.Message)"
}
finally {
  Remove-Item -Path $cLockFile -ErrorAction SilentlyContinue
}
