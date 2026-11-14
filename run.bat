@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title BetterAI

echo.
echo   ======================================
echo    BetterAI - avvio dashboard
echo   ======================================
echo.

rem --- Trova un python usabile: prima py -3, poi python ---
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%v in ('py -3 --version 2^>nul') do set "PY=py -3" & set "PYVER=%%v"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%v in ('python --version 2^>nul') do set "PY=python" & set "PYVER=%%v"
    )
)

if not defined PY (
    echo   [!!] Python non trovato nel PATH.
    echo        Installa Python 3.11+ da https://www.python.org/downloads/
    echo        ^(importante: spunta "Add Python to PATH" durante l'installazione^)
    echo.
    pause
    exit /b 1
)

echo   [i] uso: %PY%  ^(!PYVER!^)
echo.

echo   [1/3] installo dipendenze ^(numpy, flask^)...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [!!] pip install fallito. Vedi l'errore qui sopra.
    echo        Se dice "externally-managed-environment" prova:
    echo           %PY% -m pip install --user -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist data\betterai.db (
    echo.
    echo   [i] database vuoto: sync iniziale openfootball ^(~10 secondi^)...
    %PY% -m betterai sync
)

rem Auto-refresh quote se abbiamo un curl fresco o un dump json salvato.
rem Priorita' al curl (quote live), poi fallback al json salvato.
if exist curl.txt (
    echo.
    echo   [i] trovato curl.txt: ri-sincronizzo quote Sisal ^(migra db se serve^)...
    %PY% -m betterai sisal-sync --curl curl.txt
) else if exist sisal.json (
    echo.
    echo   [i] trovato sisal.json: ri-sincronizzo quote Sisal ^(migra db se serve^)...
    %PY% -m betterai sisal-sync --json sisal.json
)

echo.
echo   [2/3] apro il browser...
start "" "http://127.0.0.1:5000"

echo   [3/3] avvio il server BetterAI. CHIUDI QUESTA FINESTRA per fermarlo.
echo        ^(dal telefono: stesso WiFi, apri l'URL "dal telefono" che compare sotto^)
echo.
%PY% -m betterai web --host 0.0.0.0 --port 5000

echo.
echo   Server terminato.
pause
