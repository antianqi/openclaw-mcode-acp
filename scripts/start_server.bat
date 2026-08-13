@echo off
REM Start OpenClaw ACP Server (v5-ws)
REM HTTP: 9999, WebSocket: 9998, DB: %TEMP%\acp-tasks.db, Log: %TEMP%\acp-server.log

setlocal

REM Set defaults (override by setting env var before running)
if not defined ACP_PORT set ACP_PORT=9999
if not defined ACP_WS_PORT set ACP_WS_PORT=9998
if not defined ACP_HOST set ACP_HOST=127.0.0.1
if not defined ACP_MAX_CONCURRENT set ACP_MAX_CONCURRENT=3
if not defined ACP_TOKEN set ACP_TOKEN=openclaw-acp-demo-token

REM Kill any existing server (nuke zombies)
echo Killing existing acp-server.py processes (if any)...
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "(Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*acp-server*' }).ProcessId"') do (
    echo   killing PID %%i
    powershell -NoProfile -Command "Stop-Process -Id %%i -Force -ErrorAction SilentlyContinue"
)

REM Wait for ports to free up
timeout /t 3 /nobreak >nul

REM Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [FAIL] python not found in PATH
    exit /b 1
)

REM Start server in background (logs to %TEMP%\acp-server.log)
set LOG_FILE=%TEMP%\acp-server.log
echo Starting server...
echo   HTTP:  http://%ACP_HOST%:%ACP_PORT%
echo   WS:    ws://%ACP_HOST%:%ACP_WS_PORT%/acp/ws
echo   Log:   %LOG_FILE%
echo.

REM Use pythonw to launch without console window
start "acp-server" /B python server\acp-server.py
set SERVER_PID=%errorlevel%

REM Wait for startup
timeout /t 4 /nobreak >nul

REM Verify
echo Verifying...
python openclaw-skill\acp_cli.py health
if errorlevel 1 (
    echo [FAIL] server did not start. Check %LOG_FILE%
    exit /b 1
)

echo.
echo [OK] OpenClaw ACP Server v5-ws is running.
echo   PID: %SERVER_PID%
echo   HTTP:  http://%ACP_HOST%:%ACP_PORT%
echo   WS:    ws://%ACP_HOST%:%ACP_WS_PORT%/acp/ws
echo   Log:   %LOG_FILE%
echo.
echo Test:
echo   python openclaw-skill\acp_cli.py create --prompt "用一句话回答" --workspace "."
echo.
echo Stop:
echo   scripts\stop_server.bat