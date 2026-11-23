@echo off
setlocal
cd /d "%~dp0"
title OpenBot Polarium - Sinais ao vivo
if not exist ".venv\Scripts\activate.bat" (
  echo Ambiente nao instalado. Execute INSTALAR_WINDOWS.bat primeiro.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo ==========================================================
echo   OPENBOT POLARIUM - SINAIS AO VIVO
echo   ORDENS REAIS E DEMO DESABILITADAS
echo ==========================================================
echo.
echo 1 - Login/captura de sessao
echo 2 - Scanner ao vivo EURUSD-OTC M1/M5
echo 3 - Scanner ao vivo com ativos/timeframes configurados
echo 4 - Backtest usando somente dados capturados (sem sinteticos)
echo 5 - Ajuda
echo.
set /p op=Escolha:
if "%op%"=="1" openbot login
if "%op%"=="2" openbot scan --assets EURUSD-OTC --timeframes M1,M5 --no-llm
if "%op%"=="3" openbot scan --no-llm
if "%op%"=="4" openbot backtest --no-llm
if "%op%"=="5" openbot --help
echo.
pause
