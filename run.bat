@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

title BetterAI

echo.
echo   ======================================
echo    BetterAI - avvio dashboard
echo   ======================================
echo.

where /q python
if errorlevel 1 (
    echo   [!!] Python non trovato nel PATH.
    echo        Installa Python 3.11+ da https://www.python.org/downloads/
    echo        (ricorda di spuntare "Add Python to PATH" in fase di installazione.)
    echo.
    pause
    exit /b 1
)

echo   [1/3] installo dipendenze ^(numpy, flask^)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo   [!!] pip install fallito.
    pause
    exit /b 1
)

if not exist data\betterai.db (
    echo.
    echo   [i] database vuoto: faccio un sync iniziale di openfootball...
    python -m betterai sync
)

echo.
echo   [2/3] apro il browser...
start http://127.0.0.1:5000

echo   [3/3] lancio il server. Chiudi questa finestra per fermare.
echo.
python -m betterai web --host 0.0.0.0 --port 5000

pause
