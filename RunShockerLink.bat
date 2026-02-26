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

python -V | find /v "Python" >NUL 2>NUL && (goto :PYTHON_DOES_NOT_EXIST)
python -V | find "Python"    >NUL 2>NUL && (goto :PYTHON_DOES_EXIST)

:PYTHON_DOES_NOT_EXIST
echo Python is not installed on your system.
echo Now opening the download URL.
start "" "https://www.python.org/downloads/windows/"
goto :END

:PYTHON_DOES_EXIST
echo [%~n0] Installing requirements (Requires python 3.11+ ideally)
python -m pip install -r Requirements.txt -q
if not exist vrchat_oscquery (
    git clone https://github.com/theepicsnail/vrchat_oscquery.git
    pip install ./vrchat_oscquery -q
)

echo [%~n0] Running Shocker Link...
python VRChatShockerLink.py

:END
endlocal
pause