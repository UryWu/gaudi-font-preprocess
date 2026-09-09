@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Kill Flask (port 7500)
echo ============================================
echo  Kill all Flask processes (port 7500)
echo ============================================
echo.

rem Step 1: find PIDs listening on :7500
echo [1/2] Killing processes listening on port 7500...
set KILLED=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7500" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
    set KILLED=1
)
if "!KILLED!"=="0" echo   (none listening on 7500)

rem Step 2: fallback - kill any python running app.py (debug reloader spawns 2)
echo [2/2] Cleaning leftover python app.py processes...
for /f "delims=" %%b in ('wmic process where "name='python.exe'" get processid /value 2^>nul ^| findstr "ProcessId"') do (
    for /f "tokens=2 delims==" %%c in ("%%b") do (
        taskkill /PID %%c /F >nul 2>&1
    )
)

echo.
echo Done. Verify:
netstat -ano | findstr ":7500" >nul 2>&1
if errorlevel 1 (
    echo   OK: port 7500 is free now.
) else (
    echo   WARN: still something on 7500. Check manually:
    netstat -ano | findstr ":7500"
)
echo.
pause
