@echo off
rem Prépare l'environnement de test puis lance la suite de tests (pytest).
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "STAMP=.venv\.requirements-dev.stamp"

if not exist "%VENV_PY%" (
    echo Création de l'environnement Python...
    py -3.14 -m venv .venv 2>nul
    if not exist "%VENV_PY%" python -m venv .venv
    if not exist "%VENV_PY%" goto :err_venv
)

rem Réinstalle si requirements.txt ou requirements-dev.txt est plus récent que la dernière installation.
"%VENV_PY%" -c "import os,sys; s=r'%STAMP%'; sys.exit(0 if os.path.exists(s) and all(os.path.getmtime(s) >= os.path.getmtime(f) for f in ('requirements.txt', 'requirements-dev.txt')) else 1)"
if errorlevel 1 (
    echo Installation des dépendances de test...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r requirements-dev.txt
    if errorlevel 1 goto :err_install
    type nul > "%STAMP%"
)

"%VENV_PY%" -m pytest -q
if errorlevel 1 (
    echo.
    echo Des tests ont échoué.
    goto :pause_exit
)
echo.
echo Tous les tests sont passés.
pause
exit /b 0

:err_venv
echo.
echo ERREUR : impossible de créer l'environnement Python.
echo Vérifiez que Python 3.14 est installé : commande « py -3.14 --version ».
goto :pause_exit

:err_install
echo.
echo ERREUR : l'installation des dépendances de test a échoué.
goto :pause_exit

:pause_exit
echo.
pause
exit /b 1
