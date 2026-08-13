@echo off
REM Stop OpenClaw ACP Server (graceful kill of all acp-server.py processes)

echo Killing all acp-server.py processes...
powershell -NoProfile -Command "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*acp-server*' } | ForEach-Object { Write-Host ('  killing PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM Wait briefly
timeout /t 2 /nobreak >nul

REM Verify
echo.
echo Verifying ports are free...
powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue | Measure-Object).Count"
set /p PORT_COUNT= <nul

powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue | Measure-Object).Count" > temp_port.txt
set /p PORT_COUNT=<temp_port.txt
del temp_port.txt

if %PORT_COUNT% gtr 0 (
    echo [WARN] Port 9999 still has %PORT_COUNT% connections. Server may not have fully stopped.
    exit /b 1
)

echo [OK] OpenClaw ACP Server stopped.