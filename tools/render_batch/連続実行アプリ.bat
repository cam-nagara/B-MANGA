@echo off
rem B-Name-Render renkzoku jikkou app launcher (double-click to start)
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw "run_app.py" & goto :eof)
where pyw     >nul 2>nul && (start "" pyw     "run_app.py" & goto :eof)
where python  >nul 2>nul && (start "" python  "run_app.py" & goto :eof)
where py      >nul 2>nul && (start "" py      "run_app.py" & goto :eof)
echo Python not found. Please install Python from python.org, then try again.
pause
