#!/bin/bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -r requirements-ocr.txt
playwright install chromium
echo "Copy .env.example to .env, fill it, set insurer emails in config.yaml, then: python run.py --watch"
