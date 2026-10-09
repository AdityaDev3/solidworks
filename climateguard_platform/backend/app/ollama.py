from __future__ import annotations

import os
import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")


def status() -> dict:
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        response.raise_for_status()
        names = [m.get("name") for m in response.json().get("models", [])]
        return {"status":"ready" if any(name == OLLAMA_MODEL or name.startswith(OLLAMA_MODEL.split(":")[0] + ":") for name in names) else "model_missing",
                "model":OLLAMA_MODEL,"available_models":names}
    except requests.RequestException:
        return {"status":"offline","model":OLLAMA_MODEL,"available_models":[]}


def explain_risk(evidence: dict) -> str | None:
    if os.getenv("OLLAMA_EXPLANATIONS", "false").lower() != "true": return None
    response = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model":OLLAMA_MODEL,"stream":False,
        "messages":[
            {"role":"system","content":"You explain public-health model evidence in plain language. Only describe the supplied measured features and model score. Never change, infer, or invent measurements, probabilities, diagnoses, causes, or official outbreak status. Mention missing data and that this is an experimental early-warning estimate."},
            {"role":"user","content":f"Summarize this forecast evidence in at most 3 short sentences. Data JSON: {evidence}"}
        ],
        "options":{"temperature":0.1,"num_predict":180}
    }, timeout=45)
    response.raise_for_status()
    return str(response.json().get("message",{}).get("content","")).strip() or None
