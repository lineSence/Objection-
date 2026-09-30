# Objection! — быстрый запуск (Windows PowerShell).
#   .\start.ps1              → Web UI (http://127.0.0.1:8765)
#   .\start.ps1 ask "…"      → любая команда objection
# При первом запуске создаёт .venv и ставит пакет; повторно — только если изменился pyproject.toml.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$check = "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"

function Find-Python {
  $ErrorActionPreference = "Continue"  # PS 5.1: stderr of a probe must not become a terminating error
  foreach ($c in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3"), @("python"), @("python3"))) {
    if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
      $a = @($c | Select-Object -Skip 1) + @("-c", $check)
      & $c[0] @a 2>$null
      if ($LASTEXITCODE -eq 0) { return ,$c }
    }
  }
  return $null
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$exe = Join-Path $PSScriptRoot ".venv\Scripts\objection.exe"

if (-not (Test-Path $venvPy)) {
  $py = Find-Python
  if (-not $py) { Write-Error "Нужен Python 3.12+ — https://www.python.org/downloads/ (отметьте 'Add python.exe to PATH')" }
  Write-Host "→ Создаю окружение .venv"
  $a = @($py | Select-Object -Skip 1) + @("-m", "venv", ".venv")
  & $py[0] @a
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$stamp = (Get-FileHash pyproject.toml -Algorithm SHA256).Hash
$stampFile = ".venv\.objection-installed"
$old = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { "" }
if (-not (Test-Path $exe) -or $old -ne $stamp) {
  Write-Host "→ Устанавливаю зависимости (1–2 минуты при первом запуске)…"
  & $venvPy -m pip install -q --upgrade pip
  & $venvPy -m pip install -q -e .
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Set-Content $stampFile $stamp
}

$home_ = if ($env:OBJECTION_HOME) { $env:OBJECTION_HOME } else { Join-Path $HOME ".objection" }
if (-not $env:OBJECTION_CONFIG -and -not (Test-Path objection.yaml) -and -not (Test-Path (Join-Path $home_ "config.yaml"))) {
  Write-Host "ℹ Конфиг не найден — работают офлайн mock-модели. Свой пул моделей:"
  Write-Host "  mkdir ~\.objection; copy examples\objection.example.yaml ~\.objection\config.yaml"
}

# Plain assignment (not `$x = if …`): PowerShell would unroll a one-item array into a string, and splatting it passes single chars.
if ($args.Count -eq 0) { $cmdArgs = @("ui") } else { $cmdArgs = @($args) }
& $exe @cmdArgs
exit $LASTEXITCODE
