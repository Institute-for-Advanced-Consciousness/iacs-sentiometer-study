@echo off
pushd "%~dp0"
uv run sentiometer run -c config/local.yaml %*
popd
pause
