@echo off
setlocal

:: Resolve paths (handles spaces in directory names)
set "DOCKER_DIR=%~dp0"
set "DOCKER_DIR=%DOCKER_DIR:~0,-1%"
set "PROJECT_DIR=%DOCKER_DIR%\.."

echo [code-sandbox] Checking status...

docker inspect code-sandbox >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo Container already exists. Starting if stopped...
    docker start code-sandbox
    echo Reinitializing firewall...
    docker exec -u root code-sandbox /usr/local/bin/init-firewall.sh
) else (
    echo Building image...
    docker build -t code-sandbox-img "%DOCKER_DIR%"

    if not exist "%PROJECT_DIR%\sandbox_workspace" mkdir "%PROJECT_DIR%\sandbox_workspace"

    echo Starting new container...
    :: Load API key from project .env file
    set "API_KEY="
    for /f "tokens=1,* delims==" %%a in ('type "%PROJECT_DIR%\.env" 2^>nul') do (
        if "%%a"=="ANTHROPIC_API_KEY" set "API_KEY=%%b"
    )

    docker run -d ^
        --name code-sandbox ^
        --memory=1g ^
        --cpus=2 ^
        --pids-limit=256 ^
        --security-opt=no-new-privileges ^
        --cap-add=NET_ADMIN ^
        -e "ANTHROPIC_API_KEY=%API_KEY%" ^
        -v "%PROJECT_DIR%\sandbox_workspace:/workspace" ^
        -v "%PROJECT_DIR%:/engine:ro" ^
        code-sandbox-img

    echo Configuring network firewall...
    docker exec -u root code-sandbox /usr/local/bin/init-firewall.sh
)

echo.
echo [code-sandbox] Status:
docker ps --filter name=code-sandbox --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo.
echo Ready. Test with:
echo   docker exec code-sandbox claude -p "Say hello" --output-format json --allowedTools "Read,Write,Edit,Bash,Glob,Grep,Agent"

endlocal
