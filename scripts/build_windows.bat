@echo off
rem PrintTheShot Beta - Windows 构建脚本
cd /d "%~dp0\.."

echo Building Windows version...
if not exist .build_venv (
    python -m venv .build_venv
)
call .build_venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet -r scripts\requirements.txt pyinstaller

pyinstaller scripts\print_the_shot.spec --noconfirm
echo Done: dist\PrintTheShot.exe
