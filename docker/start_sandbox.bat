@echo off
echo [code-sandbox] Checking status...

docker inspect code-sandbox >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo Container already exists. Starting if stopped...
    docker start code-sandbox
) else (
    echo Building image...
    docker build -t code-sandbox-img "%~dp0"

    if not exist "%~dp0..\sandbox_workspace" mkdir "%~dp0..\sandbox_workspace"

    echo Starting new container...
    docker run -d ^
        --name code-sandbox ^
        --memory=1g ^
        --cpus=2 ^
        --pids-limit=256 ^
        --security-opt=no-new-privileges ^
        -v "%USERPROFILE%\.claude:/home/coder/.claude:ro" ^
        -v "%~dp0..\sandbox_workspace:/workspace" ^
        code-sandbox-img
)

echo.
echo [code-sandbox] Status:
docker ps --filter name=code-sandbox --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo.
echo Ready. Test with:
echo   docker exec code-sandbox claude -p "Say hello" --output-format json --allowedTools "Read,Write,Edit,Bash,Glob,Grep,Agent"
