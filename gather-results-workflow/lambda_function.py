import json

import boto3

AWS_REGION = "us-east-1"
S3_BUCKET = "causanatura-roc-transcriptions"

s3 = boto3.client("s3", region_name=AWS_REGION)


def lambda_handler(event, context):
    results = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET):
        for content in page["Contents"]:
            s3_filename = content["Key"]
            if s3_filename.endswith(".json"):
                obj = s3.get_object(Bucket=S3_BUCKET, Key=s3_filename)
                data = json.loads(obj["Body"].read().decode("utf-8"))

                result = {
                    "from": data.get("from"),
                    "timestamp": data.get("timestamp"),
                    "type": data.get("type"),
                }

                if result["type"] == "text":
                    result["text"] = data.get("text", {}).get("body")
                    result["audio_file"] = None
                elif result["type"] == "audio" and data.get("transcription", {}).get(
                    "ok"
                ):
                    result["text"] = data.get("transcription", {}).get("text")
                    result["audio_file"] = data.get("audio_file")
                else:
                    result["text"] = None
                    result["audio_file"] = None

                if data.get("structure", {}).get("ok"):
                    structure = data["structure"]
                    result["version"] = structure.get("version")
                    for field, value in structure.get("result", {}).items():
                        result[field] = value
                else:
                    result["version"] = None

                results.append(result)

    return {"statusCode": 200, "results": results}
