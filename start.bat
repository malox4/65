@echo off
REM Запуск графика на своём ПК (Windows).
cd /d "%~dp0"
echo График: http://127.0.0.1:8765
echo Оставьте это окно открытым. Остановка: Ctrl+C
python -m bot.server
if errorlevel 1 python3 -m bot.server
pause
