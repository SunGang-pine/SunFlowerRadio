param(
  [int]$Port = 8090,
  [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
  Write-Host "Python 3.10+ was not found. Install Python and try again." -ForegroundColor Red
  exit 1
}

$serverArgs = @("-u", "-m", "tools.server", "--port", "$Port")
if (-not $NoBrowser) { $serverArgs += "--open" }

Write-Host ""
Write-Host "  SunFlowerRadio" -ForegroundColor Yellow
Write-Host "  player: http://127.0.0.1:$Port/" -ForegroundColor Cyan
Write-Host "  studio: http://127.0.0.1:$Port/studio/" -ForegroundColor Cyan
Write-Host "  Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

& $python.Source @serverArgs
