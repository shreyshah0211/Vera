import os
import logging
from typing import Any, Dict

from flask import Flask, jsonify, request, Response
from dotenv import load_dotenv
import requests
from twilio.twiml.messaging_response import MessagingResponse
import threading
import time
import hmac
import hashlib


# Load environment variables from .env if present
load_dotenv()

app = Flask(__name__)
app.json.sort_keys = False

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("elevenlabs-api")

# Config
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_AGENT_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_AGENT_PHONE_NUMBER_ID", "")
ELEVENLABS_FROM_NUMBER = os.getenv("ELEVENLABS_FROM_NUMBER", "")
PORT = int(os.getenv("PORT", "5000"))
TRANSCRIPT_POLL_INTERVAL_SECONDS = int(os.getenv("TRANSCRIPT_POLL_INTERVAL_SECONDS", "5"))
TRANSCRIPT_POLL_TIMEOUT_SECONDS = int(os.getenv("TRANSCRIPT_POLL_TIMEOUT_SECONDS", "900"))
ELEVENLABS_WEBHOOK_SECRET = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")

ELEVEN_OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
ELEVEN_CONVO_URL_TMPL = "https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    if not ELEVENLABS_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    expected = hmac.new(ELEVENLABS_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


@app.route("/api/calls/outbound", methods=["POST"])
def outbound_call():
    """Initiate an outbound call via ElevenLabs API."""
    try:
        data = request.get_json(silent=True) or {}
        to_number = data.get("to_number")
        prompt = data.get("prompt")
        username = data.get("username")
        
        # Validate input
        if not to_number or not prompt:
            return jsonify({
                "error": "missing_parameters",
                "message": "Both 'to_number' and 'prompt' are required."
            }), 400
        
        # Validate configuration
        if not ELEVENLABS_API_KEY or not ELEVENLABS_AGENT_ID:
            return jsonify({
                "error": "server_not_configured",
                "message": "Missing ELEVENLABS_API_KEY or ELEVENLABS_AGENT_ID."
            }), 500
        
        # Build payload
        payload = {
            "agent_id": ELEVENLABS_AGENT_ID,
            "agent_phone_number_id": ELEVENLABS_AGENT_PHONE_NUMBER_ID,
            "from_number": ELEVENLABS_FROM_NUMBER,
            "to_number": to_number,
            
            # Dynamic variables go here
            "conversation_initiation_client_data": {
                "type": "conversation_initiation_client_data",
                "dynamic_variables": {
                    "purpose": prompt,
                    "username": username
                }
            }
        }
        
        # Add phone number if configured
        if ELEVENLABS_AGENT_PHONE_NUMBER_ID:
            payload["agent_phone_number_id"] = ELEVENLABS_AGENT_PHONE_NUMBER_ID
        elif ELEVENLABS_FROM_NUMBER:
            payload["from_number"] = ELEVENLABS_FROM_NUMBER
        
        # Make API call
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        
        logger.info(f"Initiating outbound call to {to_number}")
        resp = requests.post(ELEVEN_OUTBOUND_URL, headers=headers, json=payload, timeout=30)
        
        # Parse response
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text}
        
        if resp.ok:
            # For webhook mode we don't poll here; webhook will deliver transcript
            return jsonify(body), resp.status_code
        
        logger.warning(f"ElevenLabs call failed: {resp.status_code} - {body}")
        return jsonify({
            "error": "elevenlabs_error",
            "status": resp.status_code,
            "details": body
        }), resp.status_code
        
    except requests.Timeout:
        return jsonify({
            "error": "timeout",
            "message": "Request to ElevenLabs timed out."
        }), 504
        
    except Exception as e:
        logger.exception("Unexpected error in outbound call")
        return jsonify({
            "error": "internal_error",
            "message": str(e)
        }), 500


@app.route("/api/webhooks/elevenlabs", methods=["POST"])
def elevenlabs_webhook():
    """Receive post-call webhook from ElevenLabs; log transcript and return 200."""
    raw = request.data or b""
    signature = request.headers.get("ElevenLabs-Signature")

    if not verify_webhook_signature(raw, signature):
        return jsonify({"error": "invalid_signature"}), 403

    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    event_type = payload.get("type") or payload.get("event_type")
    data = payload.get("data") if isinstance(payload, dict) else None

    logger.info(f"[webhook] Received ElevenLabs event: {event_type}")

    if isinstance(data, dict):
        conversation_id = data.get("conversation_id") or data.get("id")
        transcript = data.get("transcript")
        recording_url = data.get("recording_url") or data.get("audio_url")
        status = data.get("status") or data.get("call_status")

        banner_lines = [
            f"===== ElevenLabs Webhook ({event_type}) =====",
            f"conversation_id: {conversation_id}",
        ]
        if status:
            banner_lines.append(f"status: {status}")
        if transcript:
            banner_lines.append("--- transcript ---")
            banner_lines.append(transcript)
        if recording_url:
            banner_lines.append(f"recording: {recording_url}")
        banner_lines.append("===== End Webhook =====")
        banner = "\n".join(banner_lines)

        print("\n" + banner + "\n")
        logger.info(banner)

    return jsonify({"ok": True})


@app.route("/api/conversations/<conversation_id>", methods=["GET"])
def get_conversation(conversation_id: str):
    try:
        if not ELEVENLABS_API_KEY:
            return (
                jsonify({
                    "error": "server_not_configured",
                    "message": "Missing ELEVENLABS_API_KEY in environment.",
                }),
                500,
            )

        url = ELEVEN_CONVO_URL_TMPL.format(conversation_id=conversation_id)
        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        resp = requests.get(url, headers=headers, timeout=30)

        content_type = resp.headers.get("content-type", "")
        try:
            data = resp.json() if "application/json" in content_type else {"text": resp.text}
        except Exception:
            data = {"text": resp.text}

        if not resp.ok:
            return jsonify({"error": "elevenlabs_error", "status": resp.status_code, "body": data}), resp.status_code

        transcript = data.get("transcript") if isinstance(data, dict) else None
        recording_url = data.get("recording_url") if isinstance(data, dict) else None

        return jsonify({
            "conversation_id": conversation_id,
            "transcript": transcript,
            "recording_url": recording_url,
            "raw": data if (transcript is None and recording_url is None) else None,
        }), 200
    except requests.Timeout:
        return jsonify({"error": "timeout", "message": "Request to ElevenLabs timed out."}), 504
    except Exception as e:
        logger.exception("Unexpected error while fetching conversation")
        return jsonify({"error": "internal_error", "message": str(e)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok"})

# @app.route("/reply_sms", methods=['POST'])
# def reply_sms():
#     # Create a new Twilio MessagingResponse
#     resp = MessagingResponse()
#     resp.message("Hello, this is a test reply to your SMS.")

#     # Return the TwiML (as XML) response
#     return Response(str(resp), mimetype='text/xml')

if __name__ == "__main__":
    # Bind to all interfaces to be reachable via ngrok
    app.run(host="0.0.0.0", port=PORT)
