@echo off
setlocal
cd /d %~dp0

schtasks /Create /TN "TradingCompanyV2 Dashboard" /SC ONLOGON /TR "\"%~dp0run_local.bat\"" /F
schtasks /Create /TN "TradingCompanyV2 Loop" /SC ONLOGON /TR "\"%~dp0run_company_loop.bat\"" /F

echo Registered Windows startup tasks.

