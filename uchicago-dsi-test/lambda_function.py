import json
import tempfile
import os
from datetime import date

import boto3

AWS_REGION = "us-east-1"
S3_BUCKET = "uchicago-causanatura-test"


def lambda_handler(event, context):
    socialmessaging = None
    s3 = None

    for record in event.get("Records", []):
        sns_message = record.get("Sns", {})
        whatsapp_message = json.loads(sns_message.get("Message", ""))
        payload = json.loads(whatsapp_message.get("whatsAppWebhookEntry", "{}"))

        orig_phone_id = (
            whatsapp_message.get("context", {})
            .get("MetaPhoneNumberIds", [])[0]
            .get("arn", ":")
            .split(":")[-1]
            .replace("/", "-")
        )

        print(f"Notification timestamp: {whatsapp_message.get('message_timestamp')}")

        for change in payload.get("changes", []):
            value = change.get("value", {})
            print(f"Contacts: {json.dumps(value.get('contacts', []))}")

            for message in value.get("messages", []):
                message_type = message.get("type")
                print(f"Type: {message_type}")

                if message_type == "text":
                    print(f"From: {message.get('from')}")
                    print(f"Timestamp: {message.get('timestamp')}")
                    print(f"Body: {message.get('text', {}).get('body')}")

                elif message_type == "audio":
                    audio = message["audio"]
                    timestamp = message.get("timestamp")
                    media_type = audio.get("mime_type")
                    media_id = audio.get("id")
                    try:
                        s3_dir = date.fromtimestamp(int(timestamp)).isoformat()
                    except ValueError:
                        s3_dir = None

                    print(f"From: {message.get('from')}")
                    print(f"Timestamp: {timestamp}")
                    print(f"Media-type: {media_type}")
                    print(f"Media-id: {media_id}")

                    if s3_dir is not None:
                        if socialmessaging is None:
                            socialmessaging = boto3.client(
                                "socialmessaging", region_name=AWS_REGION
                            )

                        result = socialmessaging.get_whatsapp_message_media(
                            mediaId=media_id,
                            originationPhoneNumberId=orig_phone_id,
                            destinationS3File={
                                "bucketName": S3_BUCKET,
                                "key": f"{s3_dir}/",
                            },
                        )

                        print(f"Result of copying to S3: {json.dumps(result)}")

                        if result.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
                            if s3 is None:
                                s3 = boto3.client("s3", region_name=AWS_REGION)

                            with tempfile.TemporaryDirectory() as td:
                                ext_suffix = media_type.split(";")[0].split("/")[-1]
                                s3_filename = f"{s3_dir}/{media_id}.{ext_suffix}"
                                local_filename = os.path.join(td, f"{media_id}.{ext_suffix}")
                                s3.download_file(S3_BUCKET, s3_filename, local_filename)

                                print(f"Downloaded: {os.stat(local_filename)}")



    return {"statusCode": 200}
