import os
import json
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

app = FastAPI()

FOUNDRY_RESOURCE_NAME = os.getenv("FOUNDRY_RESOURCE_NAME", "<YOUR_FOUNDRY_RESOURCE>")
FOUNDRY_DEPLOYMENT_NAME = os.getenv("FOUNDRY_DEPLOYMENT_NAME", "gpt-realtime-translate")

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default"
)

app.mount("/static", StaticFiles(directory="static"), name="static")

BASE = f"https://{FOUNDRY_RESOURCE_NAME}.openai.azure.com/openai/v1"


def mint_variants(lang: str):
    """Ephemeral-token minting attempts, in order of likelihood.

    Key insight from previous rounds:
      - /calls endpoints exist and demand ephemeral tokens (401, not 404)
      - client_secrets with type:"realtime" -> 400 (correct: not a realtime-type model)
      - client_secrets with no type       -> 400 (likely defaulted to realtime)
      - NEVER YET TRIED: explicit type:"translation" (OpenAI's documented
        session type for translation sessions)
    """
    full_audio = {"output": {"language": lang}}
    return [
        (
            "generic client_secrets + type:translation + audio",
            f"{BASE}/realtime/client_secrets",
            {"session": {"type": "translation", "model": FOUNDRY_DEPLOYMENT_NAME, "audio": full_audio}},
        ),
        (
            "generic client_secrets + type:translation (bare)",
            f"{BASE}/realtime/client_secrets",
            {"session": {"type": "translation", "model": FOUNDRY_DEPLOYMENT_NAME}},
        ),
        (
            "translations/client_secrets + full OpenAI shape",
            f"{BASE}/realtime/translations/client_secrets",
            {"session": {"model": FOUNDRY_DEPLOYMENT_NAME, "audio": full_audio}},
        ),
        (
            "translations/client_secrets + type:translation",
            f"{BASE}/realtime/translations/client_secrets",
            {"session": {"type": "translation", "model": FOUNDRY_DEPLOYMENT_NAME, "audio": full_audio}},
        ),
    ]


CALLS_URLS = [
    ("translations/calls", f"{BASE}/realtime/translations/calls?model={FOUNDRY_DEPLOYMENT_NAME}"),
    ("realtime/calls", f"{BASE}/realtime/calls?model={FOUNDRY_DEPLOYMENT_NAME}"),
]


def extract_ephemeral(data: dict):
    """GA responses put the token at top-level 'value'; some variants nest it
    under client_secret.value. Accept either."""
    if isinstance(data.get("value"), str) and data["value"]:
        return data["value"]
    cs = data.get("client_secret")
    if isinstance(cs, dict) and isinstance(cs.get("value"), str):
        return cs["value"]
    if isinstance(cs, str) and cs:
        return cs
    return None


@app.post("/connect", response_class=PlainTextResponse)
async def connect(request: Request):
    """Mint an ephemeral token (trying several session shapes), then negotiate
    the browser's SDP offer against Azure's calls endpoint with that token.
    Returns the SDP answer, or a 502 with the full diagnostic matrix."""
    sdp_offer = (await request.body()).decode("utf-8")
    lang = request.query_params.get("lang", "es")

    try:
        entra_token = token_provider()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Entra auth failed: {e}")

    diagnostics = []

    async with httpx.AsyncClient(timeout=30) as client:
        for mint_label, mint_url, payload in mint_variants(lang):
            try:
                mint_resp = await client.post(
                    mint_url,
                    headers={
                        "Authorization": f"Bearer {entra_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except Exception as e:
                diagnostics.append(f"[MINT {mint_label}] transport error: {e}")
                continue

            if mint_resp.status_code != 200:
                snippet = mint_resp.text[:300].replace("\n", " ")
                diagnostics.append(f"[MINT {mint_label}] HTTP {mint_resp.status_code}: {snippet}")
                print(f"--- mint failed: [{mint_label}] HTTP {mint_resp.status_code} ---")
                print(mint_resp.text[:500])
                continue

            data = mint_resp.json()
            ephemeral = extract_ephemeral(data)
            if not ephemeral:
                diagnostics.append(f"[MINT {mint_label}] 200 OK but no token in response: {json.dumps(data)[:300]}")
                continue

            print(f"--- EPHEMERAL TOKEN MINTED via: {mint_label} ---")

            # Token in hand: negotiate SDP with it.
            for calls_label, calls_url in CALLS_URLS:
                try:
                    sdp_resp = await client.post(
                        calls_url,
                        headers={
                            "Authorization": f"Bearer {ephemeral}",
                            "Content-Type": "application/sdp",
                        },
                        content=sdp_offer,
                    )
                except Exception as e:
                    diagnostics.append(f"[SDP {calls_label} after {mint_label}] transport error: {e}")
                    continue

                if sdp_resp.status_code in (200, 201):
                    print(f"--- SDP NEGOTIATION SUCCEEDED: mint=[{mint_label}] calls=[{calls_label}] ---")
                    return PlainTextResponse(sdp_resp.text, media_type="application/sdp")

                snippet = sdp_resp.text[:300].replace("\n", " ")
                diagnostics.append(f"[SDP {calls_label} after {mint_label}] HTTP {sdp_resp.status_code}: {snippet}")
                print(f"--- SDP failed: [{calls_label}] HTTP {sdp_resp.status_code} ---")
                print(sdp_resp.text[:500])

    detail = "All mint/negotiate strategies failed:\n" + "\n".join(diagnostics)
    print("─" * 60)
    print(detail)
    print("─" * 60)
    raise HTTPException(status_code=502, detail=detail)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
