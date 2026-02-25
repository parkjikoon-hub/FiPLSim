@echo off
title FiPLSim - Fire Protection Pipe Let Simulator
cd /d "%~dp0"
echo.
echo  ========================================
echo   FiPLSim v2.1 Starting...
echo   Fire Protection Pipe Let Simulator
echo  ========================================
echo.
echo  Browser will open automatically.
echo  To stop: press Ctrl+C in this window.
echo.
streamlit run app.py --server.port 8501
pause
