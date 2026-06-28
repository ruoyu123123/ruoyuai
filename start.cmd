@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set "SD=%~dp0core\"
set "PROJECT_DIR=%CD%"

rem -- Locate Git Bash dynamically (Git for Windows / Portable Git / Scoop / Chocolatey 都兼容) --
if not defined CLAUDE_CODE_GIT_BASH_PATH (
    for /f "tokens=*" %%i in ('where bash 2^>nul') do (
        if not defined CLAUDE_CODE_GIT_BASH_PATH set "CLAUDE_CODE_GIT_BASH_PATH=%%i"
    )
    if not defined CLAUDE_CODE_GIT_BASH_PATH (
        if exist "C:\Program Files\Git\bin\bash.exe" (
            set "CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe"
        )
    )
    if not defined CLAUDE_CODE_GIT_BASH_PATH (
        if exist "%LOCALAPPDATA%\Programs\Git\bin\bash.exe" (
            set "CLAUDE_CODE_GIT_BASH_PATH=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
        )
    )
)

rem -- Sync CLAUDE.md + commands to current project directory --
if exist "%SD%claude-home\CLAUDE.md" (
    copy /y "%SD%claude-home\CLAUDE.md" "%PROJECT_DIR%\CLAUDE.md" >nul 2>nul
)
if not exist "%PROJECT_DIR%\.claude\commands" mkdir "%PROJECT_DIR%\.claude\commands" >nul 2>nul
if exist "%~dp0.claude\commands" (
    copy /y "%~dp0.claude\commands\*.md" "%PROJECT_DIR%\.claude\commands\" >nul 2>nul
)

rem -- Launch claude CLI (interactive mode) --
rem User types anything to trigger the main menu (CLAUDE.md handles the rest)
claude --dangerously-skip-permissions --append-system-prompt "Show main menu on start. Hard rules: writing must go through /cluster-write (cluster mode, no direct chapter generation), distillation runs in cluster mode, cluster-save-state must run all 14 steps, write directly after outline without asking user, auto-switch source on fetch failure without asking user." %*
if errorlevel 1 pause
