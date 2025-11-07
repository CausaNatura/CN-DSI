import os
import json
import requests
from flask import Flask, request, abort

app = Flask(__name__)

VERIFY_TOKEN = "dev-verify-token"
WHATSAPP_API_BASE = "http://127.0.0.1:5000"
API_VERSION = "v20.0"
PHONE_NUMBER_ID = "111111111111111"

GROUP_ID = "120363040377656518@g.us"


def send_text(to: str, body: str):
    url = f"{WHATSAPP_API_BASE}/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        resp = r.json()
        msg_id = (resp.get("messages") or [{}])[0].get("id")
        print(f"[product] Sent reply → id={msg_id}")
    except Exception as e:
        print(f"[product] ERROR sending reply: {e}")


def _get(path, default=None, obj=None):
    """Tiny safe getter for nested webhook payloads."""
    obj = request.json if obj is None else obj
    cur = obj
    for key in path:
        if isinstance(cur, list):
            if not cur:
                return default
            cur = cur[0]
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


@app.get("/webhook")
def verify_webhook():
    # WhatsApp-style handshake:
    # ?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge is not None:
        return challenge, 200  # echo the raw challenge
    else:
        return "Forbidden", 403


@app.post("/webhook")
def receive_webhook():
    # Expect the standard envelope: {object, entry:[{changes:[{value:{...}}]}]}
    if not request.is_json:
        abort(400)

    # Log the full payload for debugging
    print("[product] Incoming webhook:")
    print(json.dumps(request.json, indent=2))

    value = _get(["entry", "changes", "value"])
    if not value:
        return "ignored", 200

    # 1) Inbound messages
    messages = value.get("messages") or []
    for msg in messages:
        mtype = msg.get("type")
        ts = msg.get("timestamp")
        from_id = msg.get("from")
        text = (msg.get("text") or {}).get("body")
        group_id = (msg.get("context") or {}).get("group_id")  # present in the mock
        print(
            f"[product] message: type={mtype} from={from_id} ts={ts} group={group_id} text={text!r}"
        )

        # Simple auto-reply to the "group" so you see the full loop
        if mtype == "text" and text:
            reply = f"ack: {text[:120]}"
            # Prefer the mock's group_id when present, otherwise fallback to configured GROUP_ID
            to_target = group_id or GROUP_ID
            send_text(to_target, reply)

    # 2) Status updates (sent/delivered/read)
    statuses = value.get("statuses") or []
    for st in statuses:
        print(
            f"[product] status: id={st.get('id')} status={st.get('status')} ts={st.get('timestamp')}"
        )

    return "ok", 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
