"""GPT-realtime-translate demo over WebRTC.

Transport: WebRTC. The browser opens a single peer connection to Azure that
carries the microphone audio up and the translation events down. The two HTTPS
calls below are the standard WebRTC bootstrap (token + SDP exchange).
"""

import os
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# CONFIGURATION
FOUNDRY_RESOURCE_NAME = os.getenv("FOUNDRY_RESOURCE_NAME", "<YOUR_FOUNDRY_RESOURCE>")
FOUNDRY_DEPLOYMENT_NAME = os.getenv("FOUNDRY_DEPLOYMENT_NAME", "gpt-realtime-translate")
BASE = f"https://{FOUNDRY_RESOURCE_NAME}.openai.azure.com/openai/v1"

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://ai.azure.com/.default"
)


@app.post("/connect", response_class=PlainTextResponse)
async def connect(request: Request):
    """Mint an ephemeral translation token, then exchange the browser's SDP
    offer for Azure's answer. The browser never holds a long-lived credential."""
    language = request.query_params.get("language", "es")
    offer = (await request.body()).decode("utf-8")

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Ephemeral token.
        mint = await client.post(
            f"{BASE}/realtime/translations/client_secrets",
            headers={"Authorization": f"Bearer {token_provider()}"},
            json={"session": {"model": FOUNDRY_DEPLOYMENT_NAME,
                              "audio": {"output": {"language": language}}}},
        )
        if mint.status_code != 200:
            raise HTTPException(502, f"Token request failed: {mint.text[:400]}")

        # 2. SDP exchange.
        answer = await client.post(
            f"{BASE}/realtime/translations/calls",
            headers={"Authorization": f"Bearer {mint.json()['value']}",
                     "Content-Type": "application/sdp"},
            content=offer,
        )
        if answer.status_code not in (200, 201):
            raise HTTPException(502, f"SDP exchange failed: {answer.text[:400]}")

    print(f"connected: model={FOUNDRY_DEPLOYMENT_NAME} language={language}")
    return PlainTextResponse(answer.text, media_type="application/sdp")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
