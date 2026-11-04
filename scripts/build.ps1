param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python nao encontrado: $Python. Crie .venv conforme o README ou informe -Python com o caminho completo."
}
$Python = (Resolve-Path -LiteralPath $Python).Path

& $Python -c "import sys; assert sys.version_info >= (3, 14), 'Use Python 3.14 ou superior'; import customtkinter, PIL, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "Verifique o Python e instale requirements-build.txt antes de empacotar."
}

& $Python (Join-Path $PSScriptRoot "create_icon.py")
if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel gerar o icone." }

Push-Location -LiteralPath $projectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "AgendaExecutiva.spec")
    if ($LASTEXITCODE -ne 0) { throw "O empacotamento falhou. Consulte a saida acima." }
}
finally {
    Pop-Location
}

Write-Host "Pronto: $projectRoot\dist\AgendaExecutiva\AgendaExecutiva.exe"
Write-Host "Distribua toda a pasta dist\AgendaExecutiva, incluindo _internal."
