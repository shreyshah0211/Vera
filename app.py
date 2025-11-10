import os
import logging
from typing import Any, Dict
import uuid
import queue
import datetime

from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
import requests
from twilio.twiml.messaging_response import MessagingResponse
import threading
import time
import hmac
import hashlib
import json
from pathlib import Path



# Load environment variables from .env if present
load_dotenv()

# Project root (repo root) to serve frontend assets
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Serve static files from project root at the web root path
app = Flask(
    __name__,
    static_folder=str(PROJECT_ROOT),
    static_url_path="",
)
app.json.sort_keys = False
CORS(app)  # Enable CORS for frontend requests

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("elevenlabs-api")

# Config
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_AGENT_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_AGENT_PHONE_NUMBER_ID", "")
ELEVENLABS_FROM_NUMBER = os.getenv("ELEVENLABS_FROM_NUMBER", "")
PORT = int(os.getenv("PORT", "5001"))
TRANSCRIPT_POLL_INTERVAL_SECONDS = int(os.getenv("TRANSCRIPT_POLL_INTERVAL_SECONDS", "5"))
TRANSCRIPT_POLL_TIMEOUT_SECONDS = int(os.getenv("TRANSCRIPT_POLL_TIMEOUT_SECONDS", "900"))
ELEVENLABS_WEBHOOK_SECRET = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")

ELEVEN_OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
ELEVEN_CONVO_URL_TMPL = "https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"


# Simple JSON-file based call store
CALLS_DIR = "calls"
CALLS_INDEX_FILE = os.path.join(CALLS_DIR, "index.json")

os.makedirs(CALLS_DIR, exist_ok=True)

def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()

def _normalize_transcript(value: Any) -> str | None:
    """Convert various transcript shapes to a readable string.
    - If list of strings: join with newlines
    - If list of objects: prefer 'text'/'content'/'utterance' fields per item
    - If dict with 'text' or 'content': return that
    - If string: return as-is
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in ("text", "content", "utterance", "transcript"):
            if isinstance(value.get(k), str):
                return value.get(k)
        try:
            return json.dumps(value)
        except Exception:
            return str(value)
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if isinstance(item, str):
                lines.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("utterance")
                if isinstance(text, str):
                    lines.append(text)
                else:
                    try:
                        lines.append(json.dumps(item))
                    except Exception:
                        lines.append(str(item))
            else:
                lines.append(str(item))
        return "\n".join(lines)
    try:
        return str(value)
    except Exception:
        return None

def _read_index() -> Dict[str, Any]:
    if not os.path.exists(CALLS_INDEX_FILE):
        return {"calls": []}
    try:
        with open(CALLS_INDEX_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"calls": []}

def _write_index(index: Dict[str, Any]) -> None:
    tmp = CALLS_INDEX_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, CALLS_INDEX_FILE)

def _save_call_record(call_id: str, record: Dict[str, Any]) -> None:
    path = os.path.join(CALLS_DIR, f"{call_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2)
    os.replace(tmp, path)

def _read_call_record(call_id: str) -> Dict[str, Any] | None:
    path = os.path.join(CALLS_DIR, f"{call_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


# SSE subscription management
_sse_clients: list[queue.Queue] = []

def _broadcast(event: str, data: Dict[str, Any]) -> None:
    payload = json.dumps({"event": event, "data": data})
    for q in list(_sse_clients):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    """Verify HMAC signature from ElevenLabs.
    Accepts:
      - plain hex digest
      - "sha256=<hex>"
      - Stripe-like multi-part header: "t=TIMESTAMP,v0=HEX"
    We try common payload canonicalizations seen in providers: raw, f"{t}.{raw}", f"{t}:{raw}", and f"{t}{raw}".
    If ELEVENLABS_WEBHOOK_SECRET is not set, verification is bypassed.
    """
    if not ELEVENLABS_WEBHOOK_SECRET:
        return True
    if not signature:
        return False

    secret = ELEVENLABS_WEBHOOK_SECRET.encode()
    sig_header = signature.strip()

    # Case A: Stripe-like list: "t=...,v0=..."
    if "," in sig_header and "v0=" in sig_header:
        parts = {}
        for piece in sig_header.split(","):
            if "=" in piece:
                k, v = piece.split("=", 1)
                parts[k.strip()] = v.strip()
        provided = (parts.get("v0") or "").lower()
        t = (parts.get("t") or "").strip()
        if provided and t:
            bodies = [
                raw_body,
                t.encode() + b"." + raw_body,
                t.encode() + b":" + raw_body,
                t.encode() + raw_body,
            ]
            for b in bodies:
                try:
                    cand = hmac.new(secret, b, hashlib.sha256).hexdigest().lower()
                    if hmac.compare_digest(cand, provided):
                        return True
                except Exception:
                    continue
        return False

    # Case B: "sha256=<hex>" or plain hex
    sig = sig_header
    if "=" in sig:
        try:
            _, val = sig.split("=", 1)
            sig = val.strip()
        except Exception:
            pass

    try:
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest().lower()
        return hmac.compare_digest(expected, sig.lower())
    except Exception:
        return False


@app.get("/")
def root():
    """Serve the single-page app entry point."""
    return send_from_directory(str(PROJECT_ROOT), "index.html")


@app.route("/api/calls/outbound", methods=["POST"])
def outbound_call():
    """Initiate an outbound call via ElevenLabs API.

    Accepts either:
      - call_data_file: path to JSON created by /api/finalize-call
      - or explicit fields (phone_number/call_summary or to_number/prompt)
    """
    try:
        data = request.get_json(silent=True) or {}

        # Allow server-side loading of finalized JSON to avoid client reading files
        call_data_file = data.get("call_data_file")
        loaded = None
        if call_data_file:
            # Only allow files inside the calls directory
            if not call_data_file.startswith("calls/"):
                return jsonify({
                    "error": "invalid_file",
                    "message": "call_data_file must be inside the 'calls/' directory."
                }), 400
            try:
                with open(call_data_file, "r") as f:
                    loaded = json.load(f)
            except FileNotFoundError:
                return jsonify({
                    "error": "file_not_found",
                    "message": f"File not found: {call_data_file}"
                }), 404
            except Exception as e:
                return jsonify({
                    "error": "file_read_error",
                    "message": str(e)
                }), 400

        # Normalize inputs (support both legacy and new key naming)
        to_number = (loaded or {}).get("phone_number") or data.get("phone_number") or data.get("to_number")
        prompt = (loaded or {}).get("call_summary") or data.get("call_summary") or data.get("prompt")
        receiver_name = (loaded or {}).get("receiver_name") or data.get("receiver_name")
        username = (loaded or {}).get("user_name") or data.get("user_name") or "User"
        assistant_id = data.get("assistant_id")
        call_id = str(uuid.uuid4())
        
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
                    "user_name": username,
                    "receiver_name": receiver_name,
                    # Used to correlate webhooks back to this record
                    "client_call_id": call_id
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
        
        # Create initial call record
        initial_record = {
            "id": call_id,
            "status": "ongoing",
            "to_number": to_number,
            "receiver_name": receiver_name,
            "prompt": prompt,
            "created_at": _utc_now_iso(),
            "conversation_id": None,
            "transcript": None,
            "recording_url": None,
            "assistant_id": assistant_id
        }

        # Persist to file store and index
        index = _read_index()
        index_calls = index.get("calls", [])
        index_calls.insert(0, {
            "id": call_id,
            "status": "ongoing",
            "receiver_name": receiver_name,
            "to_number": to_number,
            "created_at": initial_record["created_at"],
        })
        index["calls"] = index_calls
        _write_index(index)
        _save_call_record(call_id, initial_record)
        _broadcast("call_created", {
            "id": call_id,
            "status": "ongoing",
            "receiver_name": receiver_name,
            "to_number": to_number,
            "created_at": initial_record["created_at"],
        })

        logger.info(f"Initiating outbound call to {to_number} (call_id={call_id})")
        resp = requests.post(ELEVEN_OUTBOUND_URL, headers=headers, json=payload, timeout=30)
        
        # Parse response
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text}
        
        if resp.ok:
            # Update conversation_id if present
            conversation_id = None
            if isinstance(body, dict):
                conversation_id = body.get("conversation_id") or body.get("id")
            if conversation_id:
                record = _read_call_record(call_id) or {}
                record["conversation_id"] = conversation_id
                _save_call_record(call_id, record)
                _broadcast("call_updated", {"id": call_id, "conversation_id": conversation_id})

            # For webhook mode we don't poll here; webhook will deliver transcript
            return jsonify({"call_id": call_id, "elevenlabs": body}), resp.status_code
        
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
    # Accept multiple header casings/variants from providers
    signature = (
        request.headers.get("ElevenLabs-Signature")
        or request.headers.get("Elevenlabs-Signature")
        or request.headers.get("X-ElevenLabs-Signature")
        or request.headers.get("X-Elevenlabs-Signature")
        or request.args.get("signature")
    )

    if not verify_webhook_signature(raw, signature):
        logger.warning("Invalid webhook signature. Headers present: %s", dict(request.headers))
        return jsonify({"error": "invalid_signature"}), 403

    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"error": "invalid_json"}), 400

    event_type = payload.get("type") or payload.get("event_type")
    data = payload.get("data") if isinstance(payload, dict) else None

    logger.info(f"[webhook] Received ElevenLabs event: {event_type}")

    if isinstance(data, dict):
        # Attempt to extract identifiers and payloads from multiple possible shapes
        conversation = data.get("conversation") or {}
        conversation_id = data.get("conversation_id") or data.get("id") or conversation.get("id")

        # Transcript can appear under several keys; normalize afterwards
        transcript_raw = (
            data.get("transcript")
            or conversation.get("transcript")
            or data.get("final_transcript")
            or data.get("messages")
            or data.get("conversation_transcript")
            or data.get("utterances")
        )
        transcript = _normalize_transcript(transcript_raw)

        # Recording URL fallbacks
        recording_url = (
            data.get("recording_url")
            or data.get("audio_url")
            or conversation.get("recording_url")
            or conversation.get("audio_url")
        )
        status = data.get("status") or data.get("call_status") or conversation.get("status")

        # Try to locate record by conversation_id or dynamic client_call_id
        call_id = None
        if conversation_id:
            # Search index for a record with this conversation id (fast path: read file names)
            # Linear scan; acceptable for hackathon scale
            idx = _read_index()
            for item in idx.get("calls", []):
                rec = _read_call_record(item.get("id"))
                if rec and rec.get("conversation_id") == conversation_id:
                    call_id = item.get("id")
                    break

        # Fallback: sometimes webhook may include our dynamic variables
        dynamic = (
            data.get("dynamic_variables")
            or (data.get("conversation_initiation_client_data") or {}).get("dynamic_variables")
            or (data.get("metadata") or {}).get("dynamic_variables")
            or {}
        )
        if not call_id and isinstance(dynamic, dict):
            call_id = dynamic.get("client_call_id")

        if call_id:
            record = _read_call_record(call_id) or {"id": call_id}
            if conversation_id:
                record["conversation_id"] = conversation_id
            if transcript is not None:
                record["transcript"] = transcript
            if recording_url is not None:
                record["recording_url"] = recording_url
            if status:
                record["status"] = "finished" if status in ("completed", "finished", "ended", "completed_successfully") else status
            if record.get("status") == "finished" and "finished_at" not in record:
                record["finished_at"] = _utc_now_iso()
            _save_call_record(call_id, record)

            # If transcript became available, optionally summarize and broadcast
            if record.get("transcript"):
                try:
                    summary_prompt = (
                        "You are an assistant. Summarize only the dialogue from the following phone call transcript. "
                        "Ignore timings, latency/metric lines, events, or system messages. "
                        "Provide 3-4 extremely concise bullet points capturing what happened in the conversation, and all important details."
                    )
                    messages = [
                        {"role": "system", "content": summary_prompt},
                        {"role": "user", "content": record.get("transcript", "")},
                    ]
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=messages,
                        temperature=0.3,
                        max_tokens=350,
                    )
                    summary_text = resp.choices[0].message.content
                    record["summary"] = summary_text
                    _save_call_record(call_id, record)
                    _broadcast("call_summary", {
                        "id": call_id,
                        "assistant_id": record.get("assistant_id"),
                        "summary": summary_text,
                    })
                except Exception as e:
                    logger.exception("Failed to summarize transcript: %s", e)

            # Update index entry
            index = _read_index()
            for item in index.get("calls", []):
                if item.get("id") == call_id:
                    if status:
                        item["status"] = record.get("status", status)
                    if conversation_id:
                        item["conversation_id"] = conversation_id
                    break
            _write_index(index)

            _broadcast("call_updated", {"id": call_id, "status": record.get("status"), "conversation_id": record.get("conversation_id")})

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

        transcript = _normalize_transcript(data.get("transcript")) if isinstance(data, dict) else None
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


@app.route("/api/calls", methods=["GET"])
def list_calls():
    index = _read_index()
    return jsonify(index)


@app.route("/api/calls/<call_id>", methods=["GET"])
def get_call(call_id: str):
    record = _read_call_record(call_id)
    if not record:
        return jsonify({"error": "not_found"}), 404
    return jsonify(record)


@app.route("/api/calls/stream", methods=["GET"])
def calls_stream():
    q: queue.Queue = queue.Queue(maxsize=100)
    _sse_clients.append(q)

    def gen():
        try:
            # Send an initial comment to open the stream
            yield ": connected\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    # keep-alive
                    yield ": keepalive\n\n"
        finally:
            try:
                _sse_clients.remove(q)
            except ValueError:
                pass

    return Response(gen(), mimetype="text/event-stream")


# Initialize OpenAI client via environment variable OPENAI_API_KEY
# Ensure OPENAI_API_KEY is set in your environment or .env file
client = OpenAI()

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_message = data.get('message')
        conversation_history = data.get('history', [])
        call_details = data.get('callDetails', {})

        # Build the system prompt with call context
        system_prompt = f"""You are Vera — an intelligent, friendly personal calling assistant. 
Your goal is to help yourself (Vera) prepare for an upcoming phone call by asking short, insightful questions and guiding them to gather all necessary information.

📞 Call Context:
- Calling: {call_details.get('receiver', 'Unknown')}
- Phone: {call_details.get('phone', 'Unknown')}
- Purpose: {call_details.get('callDetails', 'Not specified')}

🎯 Your Tasks:
1. Ask clear, targeted questions to collect all information that you might need for the call.
2. Keep your responses **very short (1 to 2 sentences max)** — youre conversational but efficient.
3. Dont make scheduled calls; focus only on **preparing** yourself for the conversation.
4. Be proactive — if somethings missing or unclear, ask about it.
5. Once all key info is gathered, summarize the full call plan (with all the details provided by user such as call purpose, number, who you will be calling, when, what, etc.) make it clear.
6. End by saying:  
   “If everything looks good, type **CONFIRM** to finish.”  
   Wait for the user to type CONFIRM before closing the session.

Remember: your job is to make the user feel **confident and ready** for you making the call for them (Vera will be making the call).
"""

        # Build messages array for GPT
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        for msg in conversation_history:
            role = "user" if msg['type'] == 'user' else "assistant"
            messages.append({"role": role, "content": msg['content']})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Call OpenAI API
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # or "gpt-4" if you prefer
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )

        ai_response = response.choices[0].message.content

        return jsonify({
            "success": True,
            "response": ai_response
        })

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/initial-message', methods=['POST'])
def initial_message():
    """Generate the initial greeting message when an assistant is created"""
    try:
        data = request.json
        call_details = data.get('callDetails', {})

        system_prompt = f"""You are Vera, an intelligent personal calling assistant. Generate a brief, friendly greeting message to introduce yourself for this call preparation session.

Call Context:
- Calling: {call_details.get('receiver', 'Unknown')}
- Phone: {call_details.get('phone', 'Unknown')}
- Purpose: {call_details.get('callDetails', 'Not specified')}

Create a warm greeting (2-3 sentences) that:
1. Introduces yourself as Vera
2. Acknowledges the call context
3. Offers to help prepare for the call

Be conversational and encouraging."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0.8,
            max_tokens=150
        )

        ai_response = response.choices[0].message.content

        return jsonify({
            "success": True,
            "response": ai_response
        })

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/finalize-call', methods=['POST'])
def finalize_call():
    """Extract structured call information and save to JSON file"""
    try:
        data = request.json
        conversation_history = data.get('history', [])
        call_details = data.get('callDetails', {})
        assistant_id = data.get('assistantId')
        assistant_name = data.get('assistantName', 'Unknown')

        # Get the last AI message (before CONFIRM)
        last_ai_message = None
        for msg in reversed(conversation_history):
            if msg['type'] == 'ai':
                last_ai_message = msg['content']
                break

        if not last_ai_message:
            last_ai_message = "No AI summary available"

        # Remove the CONFIRM instruction from the message
        # Remove everything after "If everything looks good"
        if "If everything looks good" in last_ai_message:
            last_ai_message = last_ai_message.split("If everything looks good")[0].strip()

        # Create the final call data structure with only essential info
        call_data = {
            "user_name": call_details.get('userName'),
            "receiver_name": call_details.get('receiver'),
            "phone_number": call_details.get('phone'),
            "call_summary": last_ai_message
        }

        # Create calls directory if it doesn't exist
        os.makedirs('calls', exist_ok=True)

        # Create filename from assistant name (sanitize for filesystem)
        safe_name = "".join(c for c in assistant_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_')
        filename = f"calls/{safe_name}.json"

        # Save to JSON file
        with open(filename, 'w') as f:
            json.dump(call_data, f, indent=2)

        return jsonify({
            "success": True,
            "message": "Call information saved successfully!",
            "filename": filename,
            "extracted_info": last_ai_message
        })

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

if __name__ == "__main__":
    # Bind to all interfaces to be reachable via ngrok
    app.run(host="0.0.0.0", port=PORT)
