@echo off
setlocal

call "%~dp0_aws_common.bat" ec2

exit /b %ERRORLEVEL%
