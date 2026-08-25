@echo off
REM ============================================================
REM  StarBridge one-click stopper (Windows)
REM  Stops everything started by start_starbridge.bat.
REM  Primary: kill listeners on ports 8000 / 8080 (reliable even
REM  when Windows Terminal merges the console windows as tabs).
REM  Secondary: close windows by title.
REM ============================================================
echo.
echo Stopping StarBridge ...

REM 1) Kill whatever listens on 8000 / 8080 (process trees included)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do taskkill /PID %%p /T /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8080 .*LISTENING"') do taskkill /PID %%p /T /F >nul 2>&1

REM 2) Secondary: close launcher console windows by title
taskkill /FI "WINDOWTITLE eq StarBridge-Backend*"  /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq StarBridge-Core*"     /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq StarBridge-Frontend*" /T /F >nul 2>&1

echo Done. Backend / Core / Frontend stopped.
ping -n 3 127.0.0.1 >nul
