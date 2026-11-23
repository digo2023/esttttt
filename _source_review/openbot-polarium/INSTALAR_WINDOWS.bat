@echo off
setlocal
cd /d "%~dp0"
title OpenBot Polarium - Instalador Windows
echo ==========================================================
echo   OPENBOT POLARIUM 2.1.0 - INSTALACAO COMPLETA
echo   Captura real ^| Analise ^| Sinais CALL/PUT/AGUARDAR
echo   MODO SOMENTE SINAIS - ORDENS DESABILITADAS
echo ==========================================================
where py >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python Launcher nao encontrado. Instale Python 3.12 x64.
  pause
  exit /b 1
)
py -3.12 --version >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python 3.12 nao encontrado. Instale Python 3.12 x64.
  pause
  exit /b 1
)
if not exist ".venv" (
  echo [1/5] Criando ambiente virtual...
  py -3.12 -m venv .venv
) else (
  echo [1/5] Ambiente virtual existente.
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto erro
echo [2/5] Atualizando ferramentas...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto erro
echo [3/5] Instalando dependencias...
python -m pip install -r requirements.txt
if errorlevel 1 goto erro
echo [4/5] Instalando OpenBot...
python -m pip install -e .
if errorlevel 1 goto erro
echo [5/5] Instalando Chromium do Playwright...
python -m playwright install chromium
if errorlevel 1 goto erro
if not exist ".env" copy ".env.example" ".env" >nul
if not exist "logs" mkdir logs
echo.
echo INSTALACAO CONCLUIDA.
echo Execute ABRIR_POLARIUM.bat para conectar e gerar sinais.
pause
exit /b 0
:erro
echo.
echo [ERRO] Instalacao interrompida. Leia a mensagem acima.
pause
exit /b 1
