@echo off
echo [%~n0] Checking for updates...
python -m pip install --upgrade pip -q
python -m pip install -r Requirements.txt -q
if exist vrchat_oscquery (
    cd vrchat_oscquery
    git pull
    cd ..
) else (
    git clone https://github.com/theepicsnail/vrchat_oscquery.git
)
pip install ./vrchat_oscquery -q
python Updatecheck.py