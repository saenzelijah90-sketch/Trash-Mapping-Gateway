
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials


RAW_TAB = "Page 1 unprocessed"
PROCESSED_TAB = "Page 2 processed"

RAW_HEADERS = [
    "Upload date", "Latitude", "Longitude", "Image URL", "Status",
    "File size (MB)", "Width", "Height", "Camera", "ISO",
    "Exposure", "Original capture date"
]

PROCESSED_HEADERS = [
    "Upload date", "Latitude", "Longitude", "Image URL",
    "Processing status", "File size (MB)", "Width", "Height",
    "Camera", "ISO", "Exposure", "Original capture date",
    "Processed date", "Detection results"
]


def _spreadsheet_client():
    """Connect to Google Sheets for reading map reports."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()

    if not raw or not spreadsheet_id:
        raise RuntimeError(
            "Google Sheets is not configured. Check "
            "GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SPREADSHEET_ID."
        )

    info = json.loads(raw)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets"
    ]

    credentials = Credentials.from_service_account_info(
        info,
        scopes=scopes
    )

    client = gspread.authorize(credentials)
    return client.open_by_key(spreadsheet_id)


def _worksheet(spreadsheet, title, headers):
    """Find or create a worksheet and add headers if it is empty."""
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=1000,
            cols=max(14, len(headers))
        )

    if not worksheet.row_values(1):
        worksheet.append_row(
            headers,
            value_input_option="USER_ENTERED"
        )

    return worksheet


def save_submission(
    image_bytes: bytes,
    filename: str,
    mime_type: str,
    metadata: dict
) -> dict:
    """
    Send the photograph and extracted metadata to Google Apps Script.
    Apps Script saves the image in personal Google Drive and writes
    the report into the existing Google Spreadsheet.
    """

    script_url = os.environ.get(
        "GOOGLE_APPS_SCRIPT_URL", ""
    ).strip()

    upload_secret = os.environ.get(
        "UPLOAD_SECRET", ""
    ).strip()

    if not script_url:
        raise RuntimeError("GOOGLE_APPS_SCRIPT_URL is missing.")

    if not upload_secret:
        raise RuntimeError("UPLOAD_SECRET is missing.")

    if not script_url.startswith("https://script.google.com/"):
        raise RuntimeError("GOOGLE_APPS_SCRIPT_URL is not a valid Apps Script URL.")

    # Convert the original photograph to Base64.
    image_base64 = base64.b64encode(image_bytes).decode("ascii")

    # Match the field names expected by the Apps Script doPost function.
    payload = {
        "token": upload_secret,
        "imageBase64": image_base64,
        "mimeType": mime_type or "image/jpeg",
        "filename": filename or "photo",
        "lat": metadata.get("latitude"),
        "lng": metadata.get("longitude"),
        "fileSize": metadata.get("file_size_mb", ""),
        "width": metadata.get("width", ""),
        "height": metadata.get("height", ""),
        "camera": metadata.get("camera", "Unknown"),
        "iso": metadata.get("iso", "Unknown"),
        "exposure": metadata.get("exposure", "Unknown"),
        "originalDate": metadata.get("original_date", "Unknown")
    }

    request_body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        script_url,
        data=request_body,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        # urllib follows Google's Apps Script response redirect.
        with urllib.request.urlopen(request, timeout=180) as response:
            response_text = response.read().decode("utf-8")

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Apps Script HTTP error {exc.code}: {error_body[:1000]}"
        ) from exc

    except Exception as exc:
        raise RuntimeError(
            f"Could not contact Google Apps Script: {exc}"
        ) from exc

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Apps Script returned an unexpected response: "
            + response_text[:1000]
        ) from exc

    if not result.get("ok"):
        raise RuntimeError(
            "Google Apps Script could not save the photograph: "
            + str(result.get("error", "Unknown error"))
        )

    image_url = result.get("imageUrl")

    if not image_url:
        raise RuntimeError(
            "Apps Script reported success but did not return an image URL."
        )

    return {
        "image_url": image_url,
        "upload_date": datetime.now().astimezone().strftime(
            "%m/%d/%Y %H:%M:%S"
        ),
        "status": "Pending YOLO Processing",
        "drive_file_id": result.get("fileId", "")
    }


def get_map_rows(tab_name: str) -> list[dict]:
    """Read existing report rows from Google Sheets for the maps."""

    spreadsheet = _spreadsheet_client()

    headers = (
        RAW_HEADERS
        if tab_name == RAW_TAB
        else PROCESSED_HEADERS
    )

    worksheet = _worksheet(spreadsheet, tab_name, headers)
    rows = worksheet.get_all_records()

    reports = []

    for row in rows:
        try:
            lat = float(
                str(row.get("Latitude", "")).replace(",", ".")
            )
            lng = float(
                str(row.get("Longitude", "")).replace(",", ".")
            )

            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                lat = lng = None

        except (TypeError, ValueError):
            lat = lng = None

        reports.append({
            "upload_date": row.get("Upload date", ""),
            "latitude": lat,
            "longitude": lng,
            "image_url": row.get("Image URL", ""),
            "status": row.get(
                "Status",
                row.get("Processing status", "")
            ),
            "file_size_mb": row.get("File size (MB)", ""),
            "width": row.get("Width", ""),
            "height": row.get("Height", ""),
            "camera": row.get("Camera", ""),
            "iso": row.get("ISO", ""),
            "exposure": row.get("Exposure", ""),
            "original_date": row.get("Original capture date", ""),
            "processed_date": row.get("Processed date", ""),
            "detection_results": row.get("Detection results", "")
        })

    return reports
