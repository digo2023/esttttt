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

:MENU
echo.
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
echo 0 - Sair
echo.
set "op="
set /p op=Escolha:

if "%op%"=="0" goto FIM
if "%op%"=="1" openbot login
if "%op%"=="2" openbot scan --assets EURUSD-OTC --timeframes M1,M5 --no-llm
if "%op%"=="3" openbot scan --no-llm
if "%op%"=="4" openbot backtest --no-llm
if "%op%"=="5" openbot --help

if not "%op%"=="0" if not "%op%"=="1" if not "%op%"=="2" if not "%op%"=="3" if not "%op%"=="4" if not "%op%"=="5" echo Opcao invalida.

echo.
echo Operacao finalizada. Pressione uma tecla para voltar ao menu.
pause >nul
goto MENU

:FIM
echo Encerrando OpenBot Polarium...
endlocal
exit /b 0
