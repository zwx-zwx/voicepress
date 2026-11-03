@echo off
rem Start VoicePress elevated: only an elevated process can paste into admin windows
powershell -NoProfile -Command "Start-Process -FilePath 'D:\miniconda3\pythonw.exe' -ArgumentList '\"%~dp0voicepress.py\"' -Verb RunAs"
