import os
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
    "https://ai.azure.com/.default"
)

app.mount("/static", StaticFiles(directory="static"), name="static")

BASE = f"https://{FOUNDRY_RESOURCE_NAME}.openai.azure.com/openai/v1"

# Working recipe for gpt-realtime-translate over WebRTC (verified July 2026):
#
#   1. Mint an ephemeral token at /realtime/client_secrets with session type
#      "transcription" (GA supports only "realtime" and "transcription";
#      "translation" is not a registered session type). The deployment name
#      goes at session.audio.input.transcription.model - NOT session.model,
#      which the transcription schema rejects as an unknown parameter.
#
#   2. POST the browser's SDP offer to /realtime/calls with the ephemeral
#      token. Do NOT append ?model=... - the model is already bound to the
#      token, and re-specifying it makes the endpoint return HTTP 400.
#
#   3. Target output language is configured by the browser after connection,
#      via a session.update event on the "oai-events" data channel.

MINT_URL = f"{BASE}/realtime/client_secrets"

# Doc-aligned hypothesis (WebSocket guide, current): Azure binds transcription/
# translation MODELS in the connection URL, not the session payload -
#   transcription WS: /openai/v1/realtime?intent=transcription
#   translation WS:   /openai/v1/realtime/translations?model=<deployment>
# with target language set post-connect via session.update.
# WebRTC analog: mint a bare token (no model), bind the model at
# translations/calls?model=..., configure language over the data channel.
# Previous run proved: embedding the translate model as the transcription
# model in the token runs the TRANSCRIPTION pipeline, which this model
# rejects per item (it translates; it doesn't transcribe).
MINT_PAYLOADS = [
    # Bare token only. Binding the model at mint time proved to hard-wire the
    # transcription pipeline against it (per-item OperationNotSupported).
    # The model is now bound POST-CONNECT via session.update over the data
    # channel, matching the documented transcription WebSocket sample.
    ("bare transcription token (no model)",
     {"session": {"type": "transcription"}}),
]

CALLS_URLS = [
    # NOTE: ?model=<deployment> on calls endpoints returns HTTP 400 (empty
    # error body) with BOTH model-bound and modelless tokens - proven twice.
    # WebRTC calls endpoints do not accept URL model binding; removed.
    ("translations/calls (bare)", f"{BASE}/realtime/translations/calls"),
    ("realtime/calls (bare)", f"{BASE}/realtime/calls"),
]


@app.post("/connect", response_class=PlainTextResponse)
async def connect(request: Request):
    """Mint an ephemeral token, then negotiate the browser's SDP offer with
    Azure. Returns the SDP answer (application/sdp). The browser never holds
    any long-lived credential."""
    sdp_offer = (await request.body()).decode("utf-8")

    try:
        entra_token = token_provider()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Entra auth failed: {e}")

    async with httpx.AsyncClient(timeout=30) as client:
        failures = []
        for mint_label, payload in MINT_PAYLOADS:
            # Step 1: ephemeral token
            mint_resp = await client.post(
                MINT_URL,
                headers={
                    "Authorization": f"Bearer {entra_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if mint_resp.status_code != 200:
                snippet = mint_resp.text[:300].replace("\n", " ")
                failures.append(f"[MINT {mint_label}] HTTP {mint_resp.status_code}: {snippet}")
                print(f"--- mint failed: [{mint_label}] HTTP {mint_resp.status_code} ---")
                print(mint_resp.text[:500])
                continue

            data = mint_resp.json()
            ephemeral = data.get("value") or (data.get("client_secret") or {}).get("value")
            if not ephemeral:
                failures.append(f"[MINT {mint_label}] 200 OK but no token: {mint_resp.text[:300]}")
                continue

            print(f"--- EPHEMERAL TOKEN MINTED via: [{mint_label}] ---")

            # Step 2: SDP negotiation
            for calls_label, calls_url in CALLS_URLS:
                sdp_resp = await client.post(
                    calls_url,
                    headers={
                        "Authorization": f"Bearer {ephemeral}",
                        "Content-Type": "application/sdp",
                    },
                    content=sdp_offer,
                )
                if sdp_resp.status_code in (200, 201):
                    print(f"--- SDP SUCCEEDED: mint=[{mint_label}] calls=[{calls_label}] ---")
                    # Expose the deployment name so the browser can bind the
                    # model post-connect via session.update (documented pattern
                    # in the transcription WebSocket sample).
                    return PlainTextResponse(
                        sdp_resp.text,
                        media_type="application/sdp",
                        headers={"X-Model-Deployment": FOUNDRY_DEPLOYMENT_NAME},
                    )

                snippet = sdp_resp.text[:300].replace("\n", " ")
                failures.append(f"[SDP {calls_label} after {mint_label}] HTTP {sdp_resp.status_code}: {snippet}")
                print(f"--- SDP failed: [{calls_label}] HTTP {sdp_resp.status_code} ---")
                print(sdp_resp.text[:500])

    detail = "All mint/negotiate strategies failed:\n" + "\n".join(failures)
    print("─" * 60)
    print(detail)
    print("─" * 60)
    raise HTTPException(status_code=502, detail=detail)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)