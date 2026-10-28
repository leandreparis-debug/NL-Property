@echo off
rem Teste les sources de veille candidates et ouvre le rapport produit.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "STAMP=.venv\.requirements.stamp"

if not exist "%VENV_PY%" (
    echo Première utilisation : création de l'environnement Python...
    py -3.14 -m venv .venv 2>nul
    if not exist "%VENV_PY%" python -m venv .venv
    if not exist "%VENV_PY%" goto :err_venv
)

rem Réinstalle si requirements.txt est plus récent que la dernière installation.
"%VENV_PY%" -c "import os,sys; s=r'%STAMP%'; sys.exit(0 if os.path.exists(s) and os.path.getmtime(s) >= os.path.getmtime('requirements.txt') else 1)"
if errorlevel 1 (
    echo Installation des dépendances...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :err_install
    type nul > "%STAMP%"
)

echo Diagnostic des sources de veille : cela prend quelques minutes...
echo.
"%VENV_PY%" -m news_property.tools.diagnose_sources --open %*
if errorlevel 1 goto :err_run
echo.
echo Diagnostic terminé. Le rapport s'ouvre automatiquement.
pause
exit /b 0

:err_venv
echo.
echo ERREUR : impossible de créer l'environnement Python.
echo Vérifiez que Python 3.14 est installé : commande « py -3.14 --version ».
goto :pause_exit

:err_install
echo.
echo ERREUR : l'installation des dépendances a échoué.
echo Vérifiez la connexion Internet puis relancez diagnostiquer_sources.bat.
goto :pause_exit

:err_run
echo.
echo ERREUR : le diagnostic s'est arrêté anormalement - voir le message ci-dessus.
echo Le journal détaillé se trouve dans %%LOCALAPPDATA%%\NewsProperty\logs.
goto :pause_exit

:pause_exit
echo.
pause
exit /b 1
