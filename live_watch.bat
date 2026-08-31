@echo off
rem Runs the match-detail watcher. See scripts/live_watch.py for what it does.
rem ASCII only: .bat is read as CP932 on Windows, so UTF-8 Japanese here breaks the file.
cd /d "%~dp0"
if not exist "data\tmp" mkdir "data\tmp"
set PYTHONIOENCODING=utf-8
python scripts\live_watch.py >> "data\tmp\live_watch.log" 2>&1
