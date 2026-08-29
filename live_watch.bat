@echo off
rem 進行中の試合があるときだけ得点者を取りに行く。タスクスケジューラから5分おきに呼ぶ。
rem 出力は data\tmp\live_watch.log に追記される(data\tmp\ は .gitignore 対象)。
cd /d "%~dp0"
if not exist "data\tmp" mkdir "data\tmp"
python scripts\live_watch.py >> "data\tmp\live_watch.log" 2>&1
