param(
    [switch]$Execute,
    [switch]$ReturnMgame,
    [switch]$OpenGameCenter,
    [string]$Image,
    [string]$Serial,
    [ValidateSet('all', 'page', 'goods', 'promo', 'games', 'browse10', 'play20', 'listen30', 'watch60', 'timed', 'appjump')][string]$Task = 'all',
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$candidates = if ($Python) {
    @($Python)
} else {
    @(
        (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    )
}

foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        'import importlib.util as u, sys; sys.exit(0 if u.find_spec("cv2") and u.find_spec("numpy") else 1)' | & $candidate -
        if ($LASTEXITCODE -eq 0) {
            $arguments = @((Join-Path $PSScriptRoot 'coin_helper.py'), '--task', $Task)
            if ($Serial) { $arguments += @('--serial', $Serial) }
            if ($Execute) { $arguments += '--execute' }
            if ($ReturnMgame) { $arguments += '--return-mgame' }
            if ($OpenGameCenter) { $arguments += '--open-game-center' }
            if ($Image) { $arguments += @('--image', $Image) }
            & $candidate @arguments
            exit $LASTEXITCODE
        }
    }
}

throw 'No Python with OpenCV and NumPy was found. See README.md for setup.'
