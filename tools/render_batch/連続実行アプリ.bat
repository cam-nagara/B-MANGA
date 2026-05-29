@echo off
chcp 65001 >nul
rem B-Name-Render 連続実行アプリ ランチャ（このファイルをダブルクリックで起動）
cd /d "%~dp0"
where pythonw >nul 2>nul && ( start "" pythonw "run_app.py" & exit /b )
where pyw     >nul 2>nul && ( start "" pyw     "run_app.py" & exit /b )
where python  >nul 2>nul && ( start "" python  "run_app.py" & exit /b )
where py      >nul 2>nul && ( start "" py      "run_app.py" & exit /b )
echo Python が見つかりません。Python をインストールしてから、もう一度お試しください。
pause
