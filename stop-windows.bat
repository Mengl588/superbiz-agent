@echo off
chcp 65001 >nul
echo ====================================
echo Stopping SuperBizAgent services
echo ====================================
echo.

REM Stop FastAPI service.
echo [1/4] Stopping FastAPI service...
taskkill /FI "WINDOWTITLE eq SuperBizAgent API*" /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":9900 .*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
if errorlevel 1 (
    echo [INFO] FastAPI service is not running or already stopped.
) else (
    echo [OK] FastAPI service stopped.
)
echo.

REM Stop local CLS MCP service.
echo [2/4] Stopping CLS MCP service...
taskkill /FI "WINDOWTITLE eq CLS MCP Server*" /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8003 .*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
if errorlevel 1 (
    echo [INFO] CLS MCP service is not running or already stopped.
) else (
    echo [OK] CLS MCP service stopped.
)
echo.

REM Stop Monitor MCP service.
echo [3/4] Stopping Monitor MCP service...
taskkill /FI "WINDOWTITLE eq Monitor MCP Server*" /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8004 .*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
if errorlevel 1 (
    echo [INFO] Monitor MCP service is not running or already stopped.
) else (
    echo [OK] Monitor MCP service stopped.
)
echo.

REM Stop Docker containers.
echo [4/4] Stopping Milvus containers...
docker ps --format "{{.Names}}" | findstr "milvus" >nul 2>&1
if not errorlevel 1 (
    docker compose -f vector-database.yml down
    if errorlevel 1 (
        echo [ERROR] Failed to stop Docker containers.
    ) else (
        echo [OK] Milvus containers stopped.
    )
) else (
    echo [INFO] Milvus containers are not running.
)
echo.

echo ====================================
echo All services stopped.
echo ====================================
echo.
echo Tip:
echo   - To also remove Docker data volumes, run:
echo     docker compose -f vector-database.yml down -v
echo.
pause
