import base64
import json
import os
import sys
import tempfile
from datetime import datetime

import boto3

sys.path.append("./python-dependencies")
import requests

AWS_REGION = "us-east-1"
S3_BUCKET = "causanatura-roc-transcriptions"

socialmessaging = boto3.client("socialmessaging", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)


def lambda_handler(event, context):
    for record in event.get("Records", []):
        sns_message = record.get("Sns", {})
        whatsapp_message = json.loads(sns_message.get("Message", ""))
        payload = json.loads(whatsapp_message.get("whatsAppWebhookEntry", "{}"))
        whatsapp_message["whatsAppWebhookEntry"] = payload

        orig_phone_id = (
            whatsapp_message.get("context", {})
            .get("MetaPhoneNumberIds", [])[0]
            .get("arn", ":")
            .split(":")[-1]
            .replace("/", "-")
        )

        for change in payload.get("changes", []):
            value = change.get("value", {})

            for message in value.get("messages", []):
                try:
                    timestamp = int(message.get("timestamp"))
                except ValueError:
                    continue

                sender = message.get("from")

                wamid = message.get("id", "")
                if wamid.startswith("wamid."):
                    wamid = wamid[6:]
                short_id = (
                    base64.b64encode(
                        hash(base64.b64decode(wamid)).to_bytes(
                            8, byteorder="big", signed=True
                        )
                    )
                    .decode()
                    .rstrip("=")
                )

                s3_dir, output_filename = (
                    datetime.fromtimestamp(timestamp).isoformat().split("T")
                )
                output_filename = (
                    f"{sender}-{output_filename.replace(':', '-')}-{short_id}.json"
                )

                if message.get("type") == "audio":
                    audio = message["audio"]
                    media_type = audio.get("mime_type")
                    media_id = audio.get("id")

                    result = socialmessaging.get_whatsapp_message_media(
                        mediaId=media_id,
                        originationPhoneNumberId=orig_phone_id,
                        destinationS3File={
                            "bucketName": S3_BUCKET,
                            "key": f"{s3_dir}/",
                        },
                    )
                    if result.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
                        with tempfile.TemporaryDirectory() as td:
                            ext_suffix = media_type.split(";")[0].split("/")[-1]
                            s3_filename = f"{s3_dir}/{media_id}.{ext_suffix}"
                            local_filename = os.path.join(
                                td, f"{media_id}.{ext_suffix}"
                            )
                            s3.download_file(S3_BUCKET, s3_filename, local_filename)

                            with open(local_filename, "rb") as file:
                                try:
                                    transcription = requests.post(
                                        "https://api.openai.com/v1/audio/transcriptions",
                                        headers={
                                            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"
                                        },
                                        files={"file": file},
                                        data={
                                            "model": "whisper-1",
                                            "response_format": "json",
                                        },
                                    ).json()
                                    transcription["ok"] = True
                                except Exception as err:
                                    transcription = {
                                        "ok": False,
                                        "error": type(err).__name__,
                                        "message": str(err),
                                    }

                            message["audio_file"] = f"s3://{S3_BUCKET}/{s3_filename}"
                            message["transcription"] = transcription

                with tempfile.TemporaryDirectory() as td:
                    full_filename = os.path.join(td, output_filename)
                    with open(full_filename, "w") as output_file:
                        json.dump(message, output_file)
                    s3.upload_file(
                        full_filename, S3_BUCKET, f"{s3_dir}/{output_filename}"
                    )

    return {"statusCode": 200}
