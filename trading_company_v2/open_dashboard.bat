@echo off
setlocal
cd /d %~dp0

set "DASH_URL=http://134.185.118.144:8080/"
start "" "%DASH_URL%"
exit /b 0
