@echo off
setlocal

call "%~dp0_aws_common.bat" rds

exit /b %ERRORLEVEL%
