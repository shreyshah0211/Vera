# Flask + ElevenLabs Outbound Call API

This service exposes API endpoints and a webhook:

- POST `/api/calls/outbound`: Trigger an ElevenLabs ConvAI outbound call (Twilio bridge) to a phone number with a given prompt. The prompt is passed as `dynamic_variables.purpose`.
- GET `/api/conversations/{conversation_id}`: Retrieve transcript and recording URL for a conversation by proxying ElevenLabs.
- POST `/api/webhooks/elevenlabs`: Receives post-call webhooks from ElevenLabs and prints transcript/recording to the terminal.

No authentication is enforced for API calls. Optionally secure the webhook via HMAC.

## Setup

1. Python 3.10+ recommended.
2. Create and activate a virtualenv:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create environment file:
   - Copy `env.example` to `.env` and fill values
   - Required variables:
     - `ELEVENLABS_API_KEY`
     - `ELEVENLABS_AGENT_ID`
     - One of:
       - `ELEVENLABS_AGENT_PHONE_NUMBER_ID` (preferred)
       - `ELEVENLABS_FROM_NUMBER` (E.164 format, e.g., +15551234567)
     - Optional: `ELEVENLABS_WEBHOOK_SECRET` (HMAC for webhook)
     - Optional: `PORT` (default 5000)

## Run

```bash
python app.py
```

Expose via ngrok in another terminal:
```bash
ngrok http 5000
```

Use the HTTPS forwarding URL as `$NGROK` below.

## Configure ElevenLabs Webhook
- In your ElevenLabs agent settings, set the post-call webhook URL to:
  - `$NGROK/api/webhooks/elevenlabs`
- If you set `ELEVENLABS_WEBHOOK_SECRET`, configure the same secret in ElevenLabs and ensure requests include header `ElevenLabs-Signature` as HMAC SHA256 of the raw body.

## API

### Trigger outbound call
```bash
curl -sS -X POST "$NGROK/api/calls/outbound" \
  -H "Content-Type: application/json" \
  -d '{"to_number": "+15551234567", "prompt": "Get parking ticket dismissed. Reference #12345 due to unclear signage."}' | jq .
```
- The service maps `prompt` to ElevenLabs `dynamic_variables.purpose`.

### Get transcript and recording
```bash
curl -sS "$NGROK/api/conversations/<conversation_id>" | jq .
```

### Webhook (ElevenLabs -> this server)
- Example JSON (shape may vary by event):
```json
{
  "type": "post_call_transcription",
  "data": {
    "conversation_id": "conv_123",
    "status": "ended",
    "transcript": "...",
    "recording_url": "https://.../audio.mp3"
  },
  "event_timestamp": 1730000000
}
```
- The server will print a banner with transcript and recording URL to the terminal.

## Notes
- Ensure your ElevenLabs agent is configured for the Twilio outbound call bridge and expects a `purpose` dynamic variable.
- This service does not persist data; it proxies requests to ElevenLabs on demand and prints webhook data for debugging.
