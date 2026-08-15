@echo off
setlocal

for %%I in ("%~dp0..") do set "AAC_PROJECT_ROOT=%%~fI"

rem Presentation only. The package never reads the Windows UI culture;
rem this wrapper sets AAC_LANG when the caller left it unset.
if not defined AAC_LANG (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$c=(Get-Culture).Name; if ($c -match '^(zh-TW|zh_TW|zh-Hant|zh-HK|zh_HK)') { 'zh-TW' } elseif ($c -match '^(zh-CN|zh_CN|zh-Hans|zh-SG)') { 'zh-CN' } elseif ($c -match '^ja') { 'ja-JP' } elseif ($c -match '^ko') { 'ko-KR' }"`) do set "AAC_LANG=%%I"
)

uv run --project "%AAC_PROJECT_ROOT%" --locked adversarial-ai-coding %*
exit /b %errorlevel%
