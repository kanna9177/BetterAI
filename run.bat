@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title BetterAI

echo.
echo   ======================================
echo    BetterAI - avvio dashboard
echo   ======================================
echo.

rem --- 0/4: ferma server precedente (se c'e') sulla porta 5000 ---
set "KILLED="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":5000 .* LISTENING"') do (
    taskkill /F /PID %%a >nul 2>nul && set "KILLED=1"
)
if defined KILLED (
    echo   [0/4] server precedente fermato ^(era in ascolto sulla 5000^).
    timeout /t 1 /nobreak >nul
)

rem --- Pulizia cache compilato Python: evita import stantii dopo update ---
if exist betterai\__pycache__ rmdir /s /q betterai\__pycache__ 2>nul

rem --- Trova python: preferisci py -3, poi python ---
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

echo   [1/4] installo dipendenze ^(numpy, flask^)...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo   [!!] pip install fallito. Vedi errore sopra.
    pause
    exit /b 1
)

echo.
echo   [2/6] scarico calendari + risultati nuovi ^(openfootball^)...
%PY% -m betterai sync

rem --- Warmup Sisal via Playwright: rigenera curl.txt coi cookie freschi
rem     ad ogni avvio. Se playwright non e' installato (o chromium manca) fa
rem     silently fallback al curl.txt esistente.
echo.
echo   [3/6] warmup Sisal ^(rigenera curl.txt via browser headless^)...
%PY% -c "import playwright" 2>nul
if errorlevel 1 (
    echo   [i] playwright non installato: installo ^(~400 MB, una tantum^)...
    %PY% -m pip install -q playwright
    %PY% -m playwright install chromium
)
%PY% -m betterai sisal-warmup --timeout 90
if errorlevel 1 (
    echo   [!!] warmup fallito. Uso il curl.txt esistente se c'e'.
)

if exist curl.txt (
    echo.
    echo   [i] sisal-sync da curl.txt ^(auto-migra db se serve^)...
    %PY% -m betterai sisal-sync --curl curl.txt
) else if exist sisal.json (
    echo.
    echo   [i] sisal-sync da sisal.json ^(backup offline^)...
    %PY% -m betterai sisal-sync --json sisal.json
) else (
    echo   [i] nessun curl.txt / sisal.json ^(skip quote^).
)

echo.
echo   [4/6] verifico le schedine aperte sui risultati arrivati...
%PY% -m betterai settle

echo.
echo   [5/6] apro il browser...
start "" "http://127.0.0.1:5000"

echo   [6/6] avvio il server BetterAI. CHIUDI QUESTA FINESTRA per fermarlo
echo        ^(dal telefono: stesso WiFi, apri l'URL "dal telefono" qui sotto^)
echo.
%PY% -m betterai web --host 0.0.0.0 --port 5000

echo.
echo   Server terminato.
pause
