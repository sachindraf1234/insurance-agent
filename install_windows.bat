@echo off
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ocr.txt
playwright install chromium
echo Now copy .env.example to .env, fill it, set insurer emails in config.yaml, then run: python run.py --watch
pause
