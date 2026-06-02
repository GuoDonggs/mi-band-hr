@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python mi_band_hr.py
pause
