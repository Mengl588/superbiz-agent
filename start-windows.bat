@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set DEBUG=False

echo ====================================
echo Starting SuperBizAgent services
echo ====================================
echo.

REM Check whether uv is installed. Fall back to pip if uv is unavailable.
echo [1/8] Checking package manager...
where uv >nul 2>&1
if errorlevel 1 (
    echo [INFO] uv is not installed. Falling back to pip.
    echo [TIP] Installing uv can speed this up: pip install uv
    set USE_UV=0
) else (
    echo [OK] uv package manager detected.
    set USE_UV=1
)
echo.

REM Ensure the configured Python version is compatible.
echo [2/8] Checking Python version config...
if exist .python-version (
    set /p PYTHON_VERSION=<.python-version
    echo [INFO] Current configured version: !PYTHON_VERSION!
    
    REM Python 3.10 is not compatible with this project.
    echo !PYTHON_VERSION! | findstr /C:"3.10" >nul
    if not errorlevel 1 (
        echo [WARN] Python 3.10 is incompatible. Updating .python-version to 3.13...
        echo 3.13> .python-version
        echo [OK] Updated to Python 3.13.
    )
) else (
    echo [INFO] Creating .python-version...
    echo 3.13> .python-version
)
echo.

REM Create or sync the virtual environment.
echo [3/8] Creating or syncing virtual environment...
if exist .venv\Scripts\python.exe (
    echo [INFO] Virtual environment exists. Checking updates...
    
    REM Try uv sync when uv is available.
    if "%USE_UV%"=="1" (
        uv sync 2>nul
        if errorlevel 1 (
            echo [WARN] uv sync failed. Updating with pip...
            .venv\Scripts\python.exe -m pip install -e . -q
        ) else (
            echo [OK] uv sync completed.
        )
    ) else (
        echo [INFO] Updating dependencies with pip...
        .venv\Scripts\python.exe -m pip install -e . -q
    )
) else (
    echo [INFO] Creating a new virtual environment...
    
    REM Try uv sync when uv is available.
    if "%USE_UV%"=="1" (
        echo [INFO] Trying uv sync...
        uv sync 2>nul
        if not errorlevel 1 (
            echo [OK] Created with uv.
            goto :venv_created
        )
        echo [WARN] uv sync failed. Falling back to python -m venv...
    )
    
    REM Create the virtual environment with the standard venv module.
    echo [INFO] Creating with python -m venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        echo [TIP] Please make sure Python 3.11+ is installed.
        pause
        exit /b 1
    )
    
    REM Install project dependencies.
    echo [INFO] Installing project dependencies. This may take a few minutes...
    .venv\Scripts\python.exe -m pip install --upgrade pip -q
    .venv\Scripts\python.exe -m pip install -e . -q
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

:venv_created
echo [OK] Virtual environment is ready.
echo.

REM Set Python command.
set PYTHON_CMD=.venv\Scripts\python.exe

REM Start Docker Compose services.
echo [4/8] Starting Milvus vector database...
docker ps --format "{{.Names}}" | findstr "milvus-standalone" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Milvus container is already running.
) else (
    docker compose -f vector-database.yml up -d
    if errorlevel 1 (
        echo [ERROR] Docker failed to start. Please make sure Docker Desktop is running.
        pause
        exit /b 1
    )
    echo [INFO] Waiting 10 seconds for Milvus to start...
    timeout /t 10 /nobreak >nul
)
echo [OK] Milvus database is ready.
echo.

REM Start local CLS MCP server. If .env points to a real CLS MCP URL, the app will use that URL.
echo [5/8] Starting local CLS MCP server...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8003 .*LISTENING"') do (
    echo [INFO] Stopping existing process on port 8003: %%p
    taskkill /F /PID %%p >nul 2>&1
)
start "CLS MCP Server" /min %PYTHON_CMD% mcp_servers/cls_server.py
timeout /t 2 /nobreak >nul
echo [OK] Local CLS MCP server started.
echo.

REM Start local Monitor MCP server.
echo [6/8] Starting Monitor MCP server...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8004 .*LISTENING"') do (
    echo [INFO] Stopping existing process on port 8004: %%p
    taskkill /F /PID %%p >nul 2>&1
)
start "Monitor MCP Server" /min %PYTHON_CMD% mcp_servers/monitor_server.py
timeout /t 2 /nobreak >nul
echo [OK] Monitor MCP server started.
echo.

REM Start FastAPI service.
echo [7/8] Starting FastAPI service...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":9900 .*LISTENING"') do (
    echo [INFO] Stopping existing process on port 9900: %%p
    taskkill /F /PID %%p >nul 2>&1
)
start "SuperBizAgent API" %PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 9900
echo [INFO] Waiting 15 seconds for the service to start...
timeout /t 15 /nobreak >nul
echo.

REM Check service status and upload docs.
echo.
echo [INFO] Checking service status...
curl -s http://localhost:9900/health >nul 2>&1
if errorlevel 1 (
    echo [WARN] Service may still be starting. Please wait a bit.
) else (
    echo [OK] FastAPI service is running.
    echo.
    
    REM Upload aiops-docs markdown files to the vector database.
    echo [8/8] Uploading docs to vector database...
    for %%f in (aiops-docs\*.md) do (
        echo   Uploading: %%~nxf
        curl -s -X POST http://localhost:9900/api/upload -F "file=@%%f" >nul 2>&1
    )
    echo [OK] Docs uploaded.
)

echo.
echo ====================================
echo Services started.
echo ====================================
echo Web UI: http://localhost:9900
echo API docs: http://localhost:9900/docs
echo.
echo Logs:
echo   - FastAPI: logs\app_*.log
echo   - CLS MCP: type mcp_cls.log
echo   - Monitor: type mcp_monitor.log
echo Stop services: stop-windows.bat
echo ====================================
pause
