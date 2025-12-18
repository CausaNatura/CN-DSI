"""Lambda entrypoint to process WhatsApp webhook messages from SNS, enrich them, and persist results."""

import base64
import json
import os
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import boto3  # type: ignore[import-not-found]

sys.path.append("./python-dependencies")
import requests  # type: ignore[import-not-found,import-untyped]

AWS_REGION = "us-east-1"
S3_BUCKET = "causanatura-roc-transcriptions"

socialmessaging = boto3.client("socialmessaging", region_name=AWS_REGION)  # type: ignore[assignment]
s3 = boto3.client("s3", region_name=AWS_REGION)  # type: ignore[assignment]

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


def parse_sns_record(record: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Extract the WhatsApp webhook payload from an SNS record.

    Args:
        record: SNS record containing the WhatsApp webhook payload.

    Returns:
        A tuple with the parsed WhatsApp message and its payload.
    """
    sns_message = record.get("Sns", {})
    whatsapp_message = json.loads(sns_message.get("Message", ""))
    payload = json.loads(whatsapp_message.get("whatsAppWebhookEntry", "{}"))
    whatsapp_message["whatsAppWebhookEntry"] = payload
    return whatsapp_message, payload


def extract_phone_id(whatsapp_message: Dict[str, Any]) -> str:
    """Derive the origination phone id used to retrieve WhatsApp media.

    Args:
        whatsapp_message: Parsed WhatsApp message including context metadata.

    Returns:
        The normalized origination phone id.
    """
    return (
        whatsapp_message.get("context", {})
        .get("MetaPhoneNumberIds", [])[0]
        .get("arn", ":")
        .split(":")[-1]
        .replace("/", "-")
    )


def normalize_wamid(wamid: str) -> str:
    """Normalize WhatsApp message ids and produce a short stable id.

    Args:
        wamid: Original WhatsApp message id.

    Returns:
        Short, stable identifier derived from the message id.
    """
    if wamid.startswith("wamid."):
        wamid = wamid[6:]
    return (
        base64.b64encode(
            hash(base64.b64decode(wamid)).to_bytes(8, byteorder="big", signed=True)
        )
        .decode()
        .rstrip("=")
    )


def build_output_paths(timestamp: int, sender: str, short_id: str) -> Tuple[str, str]:
    """Build S3 directory and filename components from metadata.

    Args:
        timestamp: Unix timestamp from the message.
        sender: WhatsApp sender id.
        short_id: Short identifier derived from the message id.

    Returns:
        The S3 directory and the output filename.
    """
    s3_dir, output_filename = datetime.fromtimestamp(timestamp).isoformat().split("T")
    output_filename = f"{sender}-{output_filename.replace(':', '-')}-{short_id}.json"
    return s3_dir, output_filename


def parse_timestamp(message: Dict[str, Any]) -> Optional[int]:
    """Parse the WhatsApp timestamp, mirroring the original ValueError handling.

    Args:
        message: WhatsApp message payload.

    Returns:
        Parsed timestamp as int, or None if parsing fails.
    """
    timestamp_value = message.get("timestamp")
    if timestamp_value is None:
        return None
    try:
        return int(timestamp_value)
    except (ValueError, TypeError):
        return None


def request_transcription(local_filename: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Send audio to OpenAI Whisper and return the transcription payload.

    Args:
        local_filename: Path to the audio file on disk.

    Returns:
        Tuple of the transcription text (or None) and the transcription payload.
    """
    with open(local_filename, "rb") as file:
        try:
            transcription = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                timeout=TIMEOUT,
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                files={"file": file},
                data={"model": "whisper-1", "response_format": "json"},
            ).json()
            transcription["ok"] = True
            return transcription.get("text"), transcription
        except Exception as err:
            transcription = {
                "ok": False,
                "error": type(err).__name__,
                "message": str(err),
            }
            return None, transcription


def handle_audio_message(
    message: Dict[str, Any], orig_phone_id: str, s3_dir: str
) -> Optional[str]:
    """Download audio from WhatsApp, send to Whisper, and attach results to the message.

    Args:
        message: WhatsApp message containing audio metadata.
        orig_phone_id: Phone id used to fetch media from WhatsApp.
        s3_dir: S3 directory where media is stored.

    Returns:
        Transcribed message text, or None if unavailable.
    """
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
    if result.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
        return None

    with tempfile.TemporaryDirectory() as td:
        ext_suffix = media_type.split(";")[0].split("/")[-1]
        s3_filename = f"{s3_dir}/{media_id}.{ext_suffix}"
        local_filename = os.path.join(td, f"{media_id}.{ext_suffix}")
        s3.download_file(S3_BUCKET, s3_filename, local_filename)
        message_text, transcription = request_transcription(local_filename)

    message["audio_file"] = f"s3://{S3_BUCKET}/{s3_filename}"
    message["transcription"] = transcription
    return message_text


def build_structure_from_text(message_text: str) -> Dict[str, Any]:
    """Call ChatGPT to convert free text into the target JSON structure.

    Args:
        message_text: Free-text content from the WhatsApp message.

    Returns:
        Structure payload enriched with metadata and ok/error state.
    """
    try:
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
                "response_format": {"type": "json_schema", "json_schema": json_schema},
            },
        )
    except Exception as err:
        structure = {
            "ok": False,
            "error": type(err).__name__,
            "message": str(err),
        }
    else:
        result = json.loads(
            response.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "null")
        )
        if result is None:
            structure = {
                "ok": False,
                "error": None,
                "response": response.json(),
            }
        else:
            structure = {
                "ok": True,
                "result": result,
            }
    structure["version"] = version
    structure["system_message"] = system_message
    structure["json_schema"] = json_schema
    return structure


def persist_message_to_s3(
    s3_dir: str, output_filename: str, message: Dict[str, Any]
) -> None:
    """Write the enriched message to S3 using a temp file on disk.

    Args:
        s3_dir: Destination directory in S3.
        output_filename: Name of the JSON file to write.
        message: Enriched message payload to persist.
    """
    with tempfile.TemporaryDirectory() as td:
        full_filename = os.path.join(td, output_filename)
        with open(full_filename, "w") as output_file:
            json.dump(message, output_file)
        s3.upload_file(full_filename, S3_BUCKET, f"{s3_dir}/{output_filename}")


def process_message(message: Dict[str, Any], orig_phone_id: str) -> None:
    """Process a single WhatsApp message, enrich it, and persist it.

    Args:
        message: WhatsApp message payload to process.
        orig_phone_id: Phone id used to fetch media and process message.
    """
    timestamp = parse_timestamp(message)
    if timestamp is None:
        return

    sender = message.get("from") or ""
    wamid = message.get("id", "")
    short_id = normalize_wamid(wamid)

    s3_dir, output_filename = build_output_paths(timestamp, sender, short_id)

    message_text = None
    if message.get("type") == "text":
        message_text = message.get("text", {}).get("body")
    elif message.get("type") == "audio":
        message_text = handle_audio_message(message, orig_phone_id, s3_dir)

    structure = (
        build_structure_from_text(message_text) if message_text is not None else None
    )
    message["structure"] = structure
    persist_message_to_s3(s3_dir, output_filename, message)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, int]:
    """AWS Lambda entrypoint.

    Args:
        event: Lambda event containing SNS records.
        context: Lambda context (unused).

    Returns:
        HTTP-style status code dict to signal success.
    """
    for record in event.get("Records", []):
        whatsapp_message, payload = parse_sns_record(record)
        orig_phone_id = extract_phone_id(whatsapp_message)

        for change in payload.get("changes", []):
            value = change.get("value", {})

            for message in value.get("messages", []):
                process_message(message, orig_phone_id)

    return {"statusCode": 200}
