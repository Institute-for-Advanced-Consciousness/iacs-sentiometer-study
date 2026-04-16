Push-Location $PSScriptRoot
try {
    uv run sentiometer run -c config/local.yaml @args
} finally {
    Pop-Location
}
