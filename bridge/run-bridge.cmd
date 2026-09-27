@echo off
rem Windows: keeps the print bridge running and restarts it if it exits.
rem Run it from Task Scheduler "At log on" (see README.md), or double-click it.
cd /d "%~dp0"
:loop
python print_bridge.py --config bridge-config.json >> bridge.log 2>&1
timeout /t 5 /nobreak >nul
goto loop
