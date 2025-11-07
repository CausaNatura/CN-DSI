import os
import time
import json
import random
import string
import threading
import pathlib
from datetime import datetime
from uuid import uuid4
from typing import List

import requests
from flask import Flask, request, jsonify, abort

API_VERSION = "v20.0"
MOCK_WABA_ID = "999999999999999"  # arbitrary "business account id"
MOCK_PHONE_NUMBER_ID = "111111111111111"  # arbitrary "phone_number_id"
DISPLAY_PHONE_NUMBER = "+1 555-0100"

TARGET_WEBHOOK_URL = "http://localhost:5001/webhook"
VERIFY_TOKEN = "dev-verify-token"

SIM_MIN_DELAY_SEC = 5
SIM_MAX_DELAY_SEC = 15

# "Group" identifier (purely for your app’s routing – Cloud API’s webhook payload
# doesn’t include a formal group field; we tuck it into 'context' so your app can read it)
GROUP_ID = "120363040377656518@g.us"

# fake data source
DIRECTORY = pathlib.Path("~/Box/dsi-core/wff/causa-natura/Audio").expanduser()
TEXT_MESSAGES = [open(path).read() for path in DIRECTORY.glob("derived/*.txt")]
PHONE_NUMBERS = [
    ("+1 (212) 555-2368", "Janine Melnitz"),  # Ghostbusters
    ("+1 (209) 555-0176", "Marty McFly"),  # Back to the Future
    ("+1 (310) 555-2368", "Jim Rockford"),  # The Rockford Files
    ("+44 20 555-3226", "Margot Wendice"),  # Dial M for Murder
]


def wa_id_from_phone(p: str) -> str:
    digits = "".join(ch for ch in p if ch.isdigit())
    return digits or "0000000000"


def new_wamid() -> str:
    # Roughly resembles WhatsApp’s wamid format
    return f"wamid.{uuid4().hex}"


def now_timestamp() -> str:
    return str(int(time.time()))


def pick_message() -> str:
    return random.choice(TEXT_MESSAGES).strip()


def pick_sender():
    return random.choice(PHONE_NUMBERS)


def post_webhook(payload: dict):
    try:
        requests.post(TARGET_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[mock] webhook POST failed: {e}")


def wrap_in_webhook_envelope(value: dict) -> dict:
    # Mirrors WhatsApp’s webhook envelope shape (entry/changes/value). See Meta’s examples.
    # https://www.postman.com/meta/whatsapp-business-platform/folder/awnlhal/webhook-payload-reference
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": MOCK_WABA_ID, "changes": [{"field": "messages", "value": value}]}
        ],
    }


def make_inbound_payload(text_body: str, sender_phone: str, sender_name: str) -> dict:
    wa_id = wa_id_from_phone(sender_phone)
    value = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": DISPLAY_PHONE_NUMBER,
            "phone_number_id": MOCK_PHONE_NUMBER_ID,
        },
        "contacts": [{"profile": {"name": sender_name}, "wa_id": wa_id}],
        "messages": [
            {
                "from": wa_id,
                "id": new_wamid(),
                "timestamp": now_timestamp(),
                "type": "text",
                "text": {"body": text_body},
                # Not part of the official payload for Cloud API text messages; we add it
                # to help your app treat this as a "group" stream during local dev.
                "context": {"group_id": GROUP_ID},
            }
        ],
    }
    return wrap_in_webhook_envelope(value)


def make_status_payload(message_id: str, status: str) -> dict:
    # status in {"sent","delivered","read","failed"} – mirrors the 'statuses' array shape
    value = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": DISPLAY_PHONE_NUMBER,
            "phone_number_id": MOCK_PHONE_NUMBER_ID,
        },
        "statuses": [
            {
                "id": message_id,
                "status": status,
                "timestamp": now_timestamp(),
                "recipient_id": "0000000000",
            }
        ],
    }
    return wrap_in_webhook_envelope(value)


app = Flask(__name__)


@app.get("/webhook")  # WhatsApp-style verification handshake
def webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge is not None:
        # WhatsApp expects the raw challenge value echoed back.
        # https://www.postman.com/meta/whatsapp-business-platform/folder/7n518u8/step-2-set-up-webhooks
        return challenge, 200
    return "Forbidden", 403


@app.post("/webhook")  # echo for testing
def webhook_echo():
    print("[mock] received POST to /webhook (echo):", request.json)
    return "ok", 200


@app.post(f"/{API_VERSION}/<phone_number_id>/messages")
def send_message(phone_number_id):
    # Emulates the Cloud API "send" endpoint.
    # Docs + examples for the real endpoint:
    # https://www.postman.com/meta/whatsapp-business-platform/folder/4ta6erg/messages
    if phone_number_id != MOCK_PHONE_NUMBER_ID:
        return jsonify({"error": {"message": "Unknown phone_number_id in mock"}}), 404

    data = request.get_json(force=True, silent=True) or {}
    if data.get("messaging_product") != "whatsapp":
        return (
            jsonify({"error": {"message": "messaging_product must be 'whatsapp'"}}),
            400,
        )
    to = data.get("to")
    mtype = data.get("type")
    if not to or not mtype:
        return (
            jsonify({"error": {"message": "fields 'to' and 'type' are required"}}),
            400,
        )
    if mtype == "text" and not (data.get("text") or {}).get("body"):
        return (
            jsonify({"error": {"message": "text.body required for type='text'"}}),
            400,
        )

    # Create a message id similar to WhatsApp
    mid = new_wamid()
    response = {
        "messaging_product": "whatsapp",
        "contacts": [
            {"input": to, "wa_id": "".join(ch for ch in str(to) if ch.isdigit())}
        ],
        "messages": [{"id": mid}],
    }

    # Simulate async status webhooks
    def status_flow():
        time.sleep(0.3)
        post_webhook(make_status_payload(mid, "sent"))
        time.sleep(0.3)
        post_webhook(make_status_payload(mid, "delivered"))
        time.sleep(0.3)
        post_webhook(make_status_payload(mid, "read"))

    threading.Thread(target=status_flow, daemon=True).start()

    # reflect the business message back to the "group" as if others saw it
    if mtype == "text":
        body = data["text"]["body"]
        # We post a webhook event that contains the business message (from "business")
        # which some apps display alongside inbound messages.
        value = {
            "messaging_product": "whatsapp",
            "metadata": {
                "display_phone_number": DISPLAY_PHONE_NUMBER,
                "phone_number_id": MOCK_PHONE_NUMBER_ID,
            },
            "messages": [
                {
                    "from": MOCK_PHONE_NUMBER_ID,
                    "id": mid,
                    "timestamp": now_timestamp(),
                    "type": "text",
                    "text": {"body": body},
                    "context": {"group_id": GROUP_ID},
                }
            ],
        }
        threading.Thread(
            target=lambda: post_webhook(wrap_in_webhook_envelope(value)), daemon=True
        ).start()

    return jsonify(response), 200


def simulator_loop():
    print(f"[mock] simulator started; posting to {TARGET_WEBHOOK_URL}")
    while True:
        delay = random.randint(SIM_MIN_DELAY_SEC, SIM_MAX_DELAY_SEC)
        time.sleep(delay)
        msg = pick_message()
        phone, name = pick_sender()
        payload = make_inbound_payload(msg, phone, name)
        print(f"[mock] simulator: {name} -> webhook ({len(msg)} chars)")
        post_webhook(payload)


def main():
    t = threading.Thread(target=simulator_loop, daemon=True)
    t.start()
    app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
    main()
