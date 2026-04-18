@echo off
setlocal EnableExtensions

rem ============================================================
rem EDIT HERE 1:
rem By default, use the folder where this script lives.
set "DEFAULT_REPO=%~dp0"
if "%DEFAULT_REPO:~-1%"=="\" set "DEFAULT_REPO=%DEFAULT_REPO:~0,-1%"

rem EDIT HERE 2:
rem Change this message before double-clicking if needed.
set "DEFAULT_COMMIT_MESSAGE=Auto sync project files"
rem ============================================================

if /I "%~1"=="/?" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage

set "TARGET_REPO=%DEFAULT_REPO%"
set "COMMIT_MESSAGE="
set "PUSH_MODE=normal"

if not "%~1"=="" (
    if exist "%~f1\.git\" (
        set "TARGET_REPO=%~f1"
        shift /1
    ) else (
        call :looks_like_path "%~1"
        if not errorlevel 1 (
            if not exist "%~f1\" (
                echo Repository path does not exist:
                echo %~f1
                pause
                exit /b 1
            )

            echo This folder is not a Git repository:
            echo %~f1
            pause
            exit /b 1
        )
    )
)

:collect_message
if "%~1"=="" goto args_done
if /I "%~1"=="--force" (
    set "PUSH_MODE=force"
    shift /1
    goto collect_message
)
if defined COMMIT_MESSAGE (
    set "COMMIT_MESSAGE=%COMMIT_MESSAGE% %~1"
) else (
    set "COMMIT_MESSAGE=%~1"
)
shift /1
goto collect_message

:args_done
if not defined TARGET_REPO set "TARGET_REPO=%CD%"
if not defined COMMIT_MESSAGE set "COMMIT_MESSAGE=%DEFAULT_COMMIT_MESSAGE%"

pushd "%TARGET_REPO%" >nul 2>&1 || (
    echo Failed to enter repository folder:
    echo %TARGET_REPO%
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo git.exe was not found. Install Git first, then try again.
    goto :fail
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo This folder is not a Git repository:
    echo %CD%
    goto :fail
)

for /f "usebackq delims=" %%I in (`git branch --show-current`) do set "CURRENT_BRANCH=%%I"
if not defined CURRENT_BRANCH (
    echo Could not determine the current branch.
    goto :fail
)

echo Repository : %CD%
echo Branch     : %CURRENT_BRANCH%
echo Message    : %COMMIT_MESSAGE%
echo Push mode  : %PUSH_MODE%
echo.
echo Staging all changes...
git add -A
if errorlevel 1 goto :fail

git diff --cached --quiet --exit-code
if errorlevel 1 goto :has_changes
echo No local file changes to commit.
goto :push_changes

:has_changes
echo.
echo Creating commit...
git commit -m "%COMMIT_MESSAGE%"
if errorlevel 1 goto :fail

:push_changes
echo.
if /I "%PUSH_MODE%"=="force" (
    echo Force pushing local branch to GitHub with lease protection...
    echo Push will stop if origin/%CURRENT_BRANCH% has moved unexpectedly.
    git push --force-with-lease -u origin HEAD:%CURRENT_BRANCH%
) else (
    echo Pushing local branch to GitHub...
    git push -u origin HEAD:%CURRENT_BRANCH%
)
if errorlevel 1 goto :fail

echo.
echo GitHub sync completed successfully.
popd >nul
exit /b 0

:fail
echo.
echo GitHub update failed.
popd >nul
pause
exit /b 1

:looks_like_path
set "CANDIDATE=%~1"
if "%CANDIDATE%"=="." exit /b 0
if "%CANDIDATE%"==".." exit /b 0
if not "%CANDIDATE:\=%"=="%CANDIDATE%" exit /b 0
if not "%CANDIDATE:/=%"=="%CANDIDATE%" exit /b 0
echo(%CANDIDATE%| findstr /r "^[A-Za-z]:$" >nul
if not errorlevel 1 exit /b 0
exit /b 1

:usage
echo Usage:
echo   commit_github.cmd [repo_path] [commit message]
echo.
echo Double-click mode:
echo   Put commit_github.cmd in the repository root.
echo   Then just double-click commit_github.cmd.
echo.
echo Examples:
echo   commit_github.cmd "Update project files"
echo   commit_github.cmd . "Update project files"
echo   commit_github.cmd "C:\Users\USER\Documents\GitHub\Agent_Eval_Lab" "Reorganize scripts"
echo   commit_github.cmd --force "Force sync project files"
exit /b 0
