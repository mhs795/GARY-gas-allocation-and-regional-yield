@echo off
echo Setting up virtual environment...
if not exist venv (
    python -m venv venv
    call venv\Scripts\activate
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate
)

echo Launching Dashboard...
set PYTHONPATH=%PYTHONPATH%;%cd%\src
python src\dashboard.py
pause
