@echo off
setlocal


REM Check if .venv exists, if not create it
if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate venv
echo Activating virtual environment...
call ".venv\Scripts\activate.bat"

call UpdateScripts.bat

echo [%~n0] Installing requirements (Requires python 3.11+ ideally)
python -m pip install -r Requirements.txt -q
if exist vrchat_oscquery (
    cd vrchat_oscquery
    git pull
    cd ..
) else (
    git clone https://github.com/theepicsnail/vrchat_oscquery.git
)
pip install ./vrchat_oscquery -q

echo [%~n0] Running Shocker Link...
python VRChatShockerLink.py

endlocal
pause