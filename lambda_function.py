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

TIMEOUT = 20  # seconds (for each OpenAI call)
MODEL = "gpt-4.1"  # pick a *non-reasoning* model from https://platform.openai.com/docs/models
TEMPERATURE = 1.0  # randomness: from 0 to 2

# If you ever change the system message, increment this version number
version = 1

# Explicit instructions for ChatGPT
system_message = """
Cada mensaje de usuario es un informe sobre prácticas de pesca ilegal. Su tarea es identificar sus atributos principales y presentarlos como un objeto JSON:

* Hora de observación: Si se dispone de la información, incluya la hora del día en que tuvo lugar la actividad ilegal.
* Tipo Vehiculo: En caso de que se proporcione la información, ¿qué tipo de buque pesquero estaba involucrado en actividades ilegales?
* Nombre de vechiculo: En caso de que se proporcionara, ¿cuál era el número del buque pesquero?
* matricula: En caso de haberla proporcionado, ¿cuál era la matrícula del buque pesquero?
* Actividad Observada: ¿Qué actividad se observó?
* Arte De Pesca: En caso de que se haya producido, ¿qué tipo de arte de pesca ilegal se estaba llevando a cabo?
* Certeza: ¿Qué tan seguro estás de tu interpretación del texto del informe (BAJO, MEDIO, ALTO)?
* Lugar de referencia: En caso de que se haya producido, ¿dónde tuvo lugar la actividad? ¿Qué lugares de referencia había en la zona?
* Acción recomendada: ¿Qué medidas se deberían recomendar?
* palabras clave: Proporcione una lista de palabras clave que caractericen la actividad descrita en este informe.

Si no se pudo determinar un atributo a partir del texto del informe, no lo incluya en el resultado.
""".strip()

# JSON structure to force the output into (ChatGPT *mostly* conforms, but we may need to clean up small errors)
json_schema = {
    "name": "free_text_to_structure",
    "schema": {
        "type": "object",
        "properties": {
            "Hora de observación": {"type": "string"},
            "Tipo Vehiculo": {
                "type": "string",
                "enum": ["SIN_DATO", "BARCO", "BARCO_ATUNERO", "PANGA"],
            },
            "Nombre de vechiculo": {"type": "string"},
            "matricula": {"type": "string"},
            "Actividad Observada": {
                "type": "string",
                "enum": [
                    "SIN_DATO",
                    "PESCA_ZONAS_NO_PERMITIDAS",
                    "ARTES_PESCA_NO_PERMITIDAS",
                ],
            },
            "Arte De Pesca": {
                "type": "string",
                "enum": ["SIN_DATO", "RED", "BUCEO", "PISTOLA"],
            },
            "Certeza": {
                "type": "string",
                "enum": ["BAJO", "MEDIO", "ALTO"],
            },
            "Lugar de referencia": {"type": "string"},
            "Acción recomendada": {
                "type": "string",
                "enum": [
                    "Nivel de urgencia: BAJO",
                    "Nivel de urgencia: MEDIO",
                    "Nivel de urgencia: ALTO",
                ],
            },
            "palabras clave": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


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
                message_text = None

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

                if message.get("type") == "text":
                    message_text = message.get("text")

                elif message.get("type") == "audio":
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
                                        timeout=TIMEOUT,
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
                                    message_text = transcription.get("text")
                                except Exception as err:
                                    transcription = {
                                        "ok": False,
                                        "error": type(err).__name__,
                                        "message": str(err),
                                    }

                            message["audio_file"] = f"s3://{S3_BUCKET}/{s3_filename}"
                            message["transcription"] = transcription

                if message_text is not None:
                    try:
                        # ask ChatGPT to form structured output
                        response = requests.post(
                            "https://api.openai.com/v1/chat/completions",
                            timeout=TIMEOUT,
                            headers={
                                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": MODEL,
                                "temperature": TEMPERATURE,
                                "messages": [
                                    {"role": "system", "content": system_message},
                                    {"role": "user", "content": message_text},
                                ],
                                "response_format": {
                                    "type": "json_schema",
                                    "json_schema": json_schema,
                                },
                            },
                        )
                        structure = {
                            "result": json.loads(
                                response.json()["choices"][0]["message"]["content"]
                            )
                        }
                        structure["ok"] = True
                    except Exception as err:
                        structure = {
                            "ok": False,
                            "error": type(err).__name__,
                            "message": str(err),
                        }
                    structure["version"] = version
                    structure["system_message"] = system_message
                    structure["json_schema"] = json_schema

                message["structure"] = structure

                with tempfile.TemporaryDirectory() as td:
                    full_filename = os.path.join(td, output_filename)
                    with open(full_filename, "w") as output_file:
                        json.dump(message, output_file)
                    s3.upload_file(
                        full_filename, S3_BUCKET, f"{s3_dir}/{output_filename}"
                    )

    return {"statusCode": 200}
