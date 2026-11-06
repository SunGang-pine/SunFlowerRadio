param(
  [string]$Message = "",
  [string]$ShareFont = "C:/Windows/Fonts/YuGothM.ttc",
  [string]$ShareFontZh = "C:/Windows/Fonts/NotoSansSC-VF.ttf",
  [switch]$NoPush,
  [switch]$SkipShare
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot\..

$station = Get-Content station.json -Raw -Encoding UTF8 | ConvertFrom-Json
$baseUrl = [string]$station.url
$keep = 30
if ($station.audio -and $station.audio.keep_days) { $keep = [int]$station.audio.keep_days }
$cut = (Get-Date).Date.AddDays(-$keep)
$py = Get-Command python -ErrorAction SilentlyContinue

$tmp = Join-Path $env:TEMP ("sfr-pages-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
  # only what the static station needs (no tools, studio, .env.local or docs)
  foreach ($item in @("index.html", "station.json", "episodes.json", "logo.svg", "LICENSE")) {
    if (Test-Path $item) { Copy-Item $item $tmp }
  }
  foreach ($dir in @("episodes", "character", "voices")) {
    if (Test-Path $dir) { Copy-Item $dir $tmp -Recurse }
  }

  # voice audition clips are generated, not committed
  if ((Test-Path "voices/index.html") -and $py) {
    $sampleDir = Join-Path $tmp "voices\samples"
    & $py.Source -m tools.voice_samples --out $sampleDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "voice samples: skipped (Edge TTS unavailable)" -ForegroundColor DarkYellow }
    $mmxDir = Join-Path $tmp "voices\minimax\samples"
    & $py.Source -m tools.minimax_samples --out $mmxDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "MiniMax samples: skipped (no key or API unavailable)" -ForegroundColor DarkYellow }
  }

  # recent audio only: audio/<YYYY-MM-DD>-<lang>.mp3
  $audioDir = Join-Path $tmp "audio"
  New-Item -ItemType Directory -Force -Path $audioDir | Out-Null
  $n = 0
  if (Test-Path "audio") {
    Get-ChildItem "audio" -File | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}' } | ForEach-Object {
      $d = [datetime]::ParseExact($_.Name.Substring(0, 10), "yyyy-MM-dd", $null)
      if ($d -ge $cut) { Copy-Item $_.FullName $audioDir; $n++ }
    }
  }

  # share cards with the public URL
  if (-not $SkipShare) {
    if ($py) {
      $shareArgs = @("tools/share.py", $tmp, "--all", "--url", $baseUrl)
      if (Test-Path $ShareFont) { $shareArgs += @("--font", $ShareFont) }
      if (Test-Path $ShareFontZh) { $shareArgs += @("--font-zh", $ShareFontZh) }
      & $py.Source @shareArgs
    } else {
      Write-Host "share cards: skipped (python not found)" -ForegroundColor DarkYellow
    }
  }

  New-Item -ItemType File -Force -Path (Join-Path $tmp ".nojekyll") | Out-Null
  Push-Location $tmp
  try {
    git init -q -b gh-pages
    git add -A -f
    $msg = if ($Message) { $Message } else { "publish " + (Get-Date -Format "yyyy-MM-dd HH:mm") }
    git -c user.name="$($station.name)" -c user.email="noreply@github.com" commit -q -m $msg
    if (-not $NoPush) {
      $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
      $remote = (git -C $repoRoot remote get-url origin)
      $env:HTTP_PROXY = ""
      $env:HTTPS_PROXY = ""
      $env:ALL_PROXY = ""
      git -c http.proxy= -c https.proxy= push -q -f $remote gh-pages
      Write-Host ""
      Write-Host "published gh-pages: $((git ls-files | Measure-Object).Count) files, $n audio" -ForegroundColor Green
      Write-Host "site: $baseUrl" -ForegroundColor Cyan
    } else {
      $count = (git ls-files | Measure-Object).Count
      Write-Host "built gh-pages in $tmp ($count files, $n audio; not pushed)" -ForegroundColor Yellow
    }
  } finally {
    Pop-Location
  }
} finally {
  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
}
