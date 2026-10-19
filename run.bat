@echo off
chcp 65001 >nul
cd /d "%~dp0"
python trend_site.py --open
pause
