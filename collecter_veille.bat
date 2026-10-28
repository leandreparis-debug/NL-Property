@echo off
rem Collecte la veille du mois (par défaut le mois précédent).
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

set "MOIS="
rem Mois facultatif en argument (ex. collecter_veille.bat 2026-10) ; sinon mois précédent.
if "%~1"=="" (
    set /p "MOIS=Mois à collecter (AAAA-MM, Entrée = mois précédent) : "
) else (
    set "MOIS=%~1"
)
echo Collecte de la veille : cela peut prendre plusieurs minutes...
echo.
if defined MOIS (
    "%VENV_PY%" -m news_property.tools.collect --month "%MOIS%"
) else (
    "%VENV_PY%" -m news_property.tools.collect
)
if errorlevel 1 goto :err_run
echo.
echo Collecte terminée. Les articles sont prêts à être relus dans l'application.
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
echo Vérifiez la connexion Internet puis relancez collecter_veille.bat.
goto :pause_exit

:err_run
echo.
echo ERREUR : la collecte n'a pas abouti - voir le message ci-dessus.
echo Le journal détaillé se trouve dans %%LOCALAPPDATA%%\NewsProperty\logs.
goto :pause_exit

:pause_exit
echo.
pause
exit /b 1
