@echo off
if "%~1"=="" (
  echo Usage: apply-import.bat packet.json
  exit /b 1
)
"%~dp0..\.venv\Scripts\python.exe" "%~dp0import_packet.py" --apply %*
