@echo off
rem Installe la dernière proposition du diagnostic (sources.proposed.yaml) comme liste
rem des sources de la collecte (config\sources.yaml), après vérification du fichier.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo L'environnement Python n'existe pas encore : lancez d'abord diagnostiquer_sources.bat.
    goto :pause_exit
)

echo Installation de la liste des sources proposée par le dernier diagnostic...
echo.
"%VENV_PY%" -m news_property.tools.diagnose_sources --install-proposal
if errorlevel 1 goto :err_run
echo.
pause
exit /b 0

:err_run
echo.
echo ERREUR : la liste n'a pas été installée - voir le message ci-dessus.
echo Si aucune proposition n'existe, lancez d'abord diagnostiquer_sources.bat.
goto :pause_exit

:pause_exit
echo.
pause
exit /b 1
