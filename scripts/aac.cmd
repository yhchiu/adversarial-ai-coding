@echo off
setlocal

for %%I in ("%~dp0..") do set "AAC_PROJECT_ROOT=%%~fI"

uv run --project "%AAC_PROJECT_ROOT%" --locked adversarial-ai-coding %*
exit /b %errorlevel%
