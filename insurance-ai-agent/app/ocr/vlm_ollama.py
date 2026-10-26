"""OPTIONAL last-resort reader for very unclear documents: a FREE local Vision-LLM.

Uses Ollama (https://ollama.com) running on YOUR computer — no cloud, no API key,
no data leaves the PC. Recommended model:  ollama pull qwen2.5vl:7b
Only invoked when classic OCR confidence is low. The model is told to output
JSON and to copy text EXACTLY, never invent. Result is still human-reviewed.
"""
from __future__ import annotations
import base64, json

def vlm_extract(png: bytes, kind: str, host: str = "http://localhost:11434",
                model: str = "qwen2.5vl:7b", timeout: int = 180) -> dict | None:
    prompts = {
        "passport": ("Read this Sri Lanka passport photo page. Reply ONLY JSON: "
                     '{"surname":"","given_names":"","passport_no":"","birthday":"YYYY-MM-DD",'
                     '"sex":"M/F","mrz_line1":"","mrz_line2":""}. Copy exactly. Empty string if unreadable.'),
        "police": ("Read this Sri Lanka Police Clearance. Reply ONLY JSON: "
                   '{"full_name":"","passport_no":"","nic":"","address":""}. '
                   "Address = the full line after 'Address:'. Copy exactly."),
        "application": ('Reply ONLY JSON: {"telephone":"","full_name":""}. Telephone = Sri Lanka mobile if present.'),
    }
    try:
        import requests
        r = requests.post(f"{host}/api/generate", timeout=timeout, json={
            "model": model, "stream": False,
            "prompt": prompts.get(kind, prompts["application"]),
            "images": [base64.b64encode(png).decode()],
            "options": {"temperature": 0},
        })
        r.raise_for_status()
        raw = r.json().get("response", "")
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1]) if start >= 0 else None
    except Exception:
        return None  # Ollama not running -> silently skip, classic OCR result stands
