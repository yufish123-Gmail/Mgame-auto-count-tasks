@echo off
setlocal
title Mgame Auto Count Tasks
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Task all -Execute
set "task_exit=%ERRORLEVEL%"
if not "%task_exit%"=="0" echo Run stopped with exit code %task_exit%. See the message and run log above.
pause
exit /b %task_exit%
