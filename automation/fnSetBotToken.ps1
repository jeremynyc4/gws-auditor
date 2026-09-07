# >>>>> fnSetBotToken.ps1 <<<<<
# Prompts for the claudebot-ymerej personal access token, stores it as the
# user-level GH_TOKEN_BOT environment variable, and then proves it works by
# asking GitHub who the token belongs to.
#
# Written because the original one-liner failed silently: a masked prompt shows
# nothing as you type, so an empty paste looked exactly like a successful one and
# quietly stored nothing. Every step here reports success or failure out loud.
#
# The token is never echoed to the screen and never written to a file.

$cExpectedLogin_str = 'claudebot-ymerej'
$cVarName_str       = 'GH_TOKEN_BOT'
$cScope_str         = 'User'
$cMinLength_int     = 20

# >>>>> fnReadToken <<<<<
# Reads the token without echoing it, and converts it back to plain text so it
# can be handed to gh. Returns an empty string if nothing was entered.
function fnReadToken {
  $vSecure_obj = Read-Host "Paste the claudebot-ymerej token (input is hidden)" -AsSecureString
  if (-not $vSecure_obj) { return '' }
  $vPointer_obj = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($vSecure_obj)
  try {
    $vPlain_str = [Runtime.InteropServices.Marshal]::PtrToStringAuto($vPointer_obj)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($vPointer_obj)
  }
  if (-not $vPlain_str) { return '' }
  return $vPlain_str.Trim()
}

$vToken_str = fnReadToken

if (-not $vToken_str) {
  Write-Host "FAILED: nothing was entered. Nothing has been changed." -ForegroundColor Red
  Write-Host "Tip: some terminals ignore Ctrl+V at a hidden prompt -- try right-click to paste." -ForegroundColor Yellow
  exit 1
}

if ($vToken_str.Length -lt $cMinLength_int) {
  Write-Host "FAILED: that is only $($vToken_str.Length) characters, too short for a GitHub token." -ForegroundColor Red
  Write-Host "Nothing has been changed." -ForegroundColor Red
  exit 1
}

if (-not ($vToken_str.StartsWith('ghp_') -or $vToken_str.StartsWith('github_pat_'))) {
  Write-Host "WARNING: that does not start with 'ghp_' or 'github_pat_'." -ForegroundColor Yellow
  Write-Host "Continuing anyway, but the check below will catch it if it is wrong." -ForegroundColor Yellow
}

# Verify BEFORE storing, so a bad token never gets persisted.
$env:GH_TOKEN = $vToken_str
Write-Host ""
Write-Host "Asking GitHub who this token belongs to..." -ForegroundColor Cyan
$vLogin_str = (& gh api user --jq '.login' | Out-String).Trim()

if (-not $vLogin_str) {
  Write-Host "FAILED: GitHub did not accept the token (no login returned)." -ForegroundColor Red
  Write-Host "Nothing has been stored." -ForegroundColor Red
  $env:GH_TOKEN = $null
  exit 1
}

if ($vLogin_str -ne $cExpectedLogin_str) {
  Write-Host "FAILED: this token belongs to '$vLogin_str', not '$cExpectedLogin_str'." -ForegroundColor Red
  Write-Host "You were probably signed in as yourself when you created it." -ForegroundColor Yellow
  Write-Host "Nothing has been stored." -ForegroundColor Red
  $env:GH_TOKEN = $null
  exit 1
}

[Environment]::SetEnvironmentVariable($cVarName_str, $vToken_str, $cScope_str)
$vStored_str = [Environment]::GetEnvironmentVariable($cVarName_str, $cScope_str)

if (-not $vStored_str) {
  Write-Host "FAILED: the variable did not persist. Nothing was stored." -ForegroundColor Red
  $env:GH_TOKEN = $null
  exit 1
}

$env:GH_TOKEN = $null
Write-Host ""
Write-Host "SUCCESS." -ForegroundColor Green
Write-Host "  token belongs to : $vLogin_str" -ForegroundColor Green
Write-Host "  stored as        : $cVarName_str ($cScope_str scope, $($vStored_str.Length) chars)" -ForegroundColor Green
Write-Host ""
Write-Host "Tell Claude it is done; nothing else for you to do." -ForegroundColor Cyan
