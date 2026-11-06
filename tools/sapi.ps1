param(
  [string]$Job = "",
  [switch]$List
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$installed = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })

if ($List) {
  $synth.Dispose()
  @{ ok = $true; voices = $installed } | ConvertTo-Json -Compress
  exit 0
}

if (-not $Job) { throw "job file is required" }
$data = Get-Content -LiteralPath $Job -Raw -Encoding UTF8 | ConvertFrom-Json
$outDir = [string]$data.outDir
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$wanted = [string]$data.voice
$chosen = $null
if ($wanted) {
  $chosen = $installed | Where-Object { $_ -eq $wanted } | Select-Object -First 1
  if (-not $chosen) { $chosen = $installed | Where-Object { $_ -like "*$wanted*" } | Select-Object -First 1 }
}
if ($chosen) {
  try { $synth.SelectVoice($chosen) } catch { $chosen = $null }
}
if (-not $chosen) { $chosen = $synth.Voice.Name }

$rate = 0
if ($null -ne $data.rate) { $rate = [int]$data.rate }
if ($rate -lt -10) { $rate = -10 }
if ($rate -gt 10) { $rate = 10 }
$synth.Rate = $rate

$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
  44100,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono
)

foreach ($it in $data.items) {
  $path = Join-Path $outDir ("{0}.wav" -f $it.id)
  $synth.SetOutputToWaveFile($path, $fmt)
  $synth.Speak([string]$it.text)
}
$synth.SetOutputToNull()
$synth.Dispose()
@{ ok = $true; voice = $chosen; rate = $rate; voices = $installed } | ConvertTo-Json -Compress
