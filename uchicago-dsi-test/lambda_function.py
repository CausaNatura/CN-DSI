import json
from datetime import date

import boto3

SOCIAL_REGION = "us-east-1"
S3_BUCKET = "uchicago-causanatura-test"

def lambda_handler(event, context):
    client = boto3.client("socialmessaging", region_name=SOCIAL_REGION)

    # parse these from the SNS payload
    media_id = "792642010493192"
    orig_phone_id = "phone-number-id-b43cdd61030c4f538a567f97822b2635"
    key = date.today().isoformat() + "/"

    resp = client.get_whatsapp_message_media(
        mediaId=media_id,
        originationPhoneNumberId=orig_phone_id,
        destinationS3File={"bucketName": S3_BUCKET, "key": key},
    )

    return {"statusCode": 200, "body": resp}
