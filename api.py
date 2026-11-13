
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import fitz
import ocrust

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("atelier")


# Moteur OCR configuré pour limiter la consommation de mémoire.
# Le modèle reconnaît le français et l'anglais.
ocr_engine = ocrust.Ocr(
    lang="fr,en",
    page_workers=1,
    pdf_dpi=150,
    det_limit_side=640,
    rec_batch_size=1,
    word_boxes=False,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "25"))


app = FastAPI(
    title="Atelier — API de traitement documentaire",
    version="1.0.0"
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "atelier"}


@app.get("/Verification/", include_in_schema=False)
async def legacy_verification() -> dict[str, str]:
    # Ancienne URL conservée pour compatibilité avec d'anciens scripts.
    return {"status": "ok"}


def convert_ocr_result(document: Any) -> list[dict[str, Any]]:
    """Convertit les résultats OCRust au format utilisé par l'interface."""

    blocks = []

    for line in document.lines:
        text = str(line.text).strip()

        if not text:
            continue

        x1, y1, x2, y2 = line.box.as_tuple()

        blocks.append({
            "text": text,
            "confidence": round(float(line.confidence), 3),
            "box": [
                int(x1),
                int(y1),
                int(x2),
                int(y2)
            ],
        })

    return blocks


def recognize_image(image: Image.Image, language: str) -> list[dict[str, Any]]:
    """Reconnaissance OCR avec OCRust."""

    try:
        document = ocr_engine.scan(image, name="image")
        return convert_ocr_result(document)

    except Exception as exc:
        logger.exception("Erreur OCRust sur une image")
        raise RuntimeError(
            f"Erreur pendant la reconnaissance OCR : {exc}"
        ) from exc


def extract_from_image(
    data: bytes,
    language: str
) -> tuple[str, list[dict[str, Any]]]:

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()

            blocks = recognize_image(image, language)

    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Le fichier image est illisible ou endommagé."
        ) from exc

    text = "\n".join(block["text"] for block in blocks)

    return text, blocks


def extract_from_pdf(
    data: bytes,
    language: str
) -> tuple[str, list[dict[str, Any]], int]:

    try:
        document = fitz.open(stream=data, filetype="pdf")

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Le PDF est invalide ou endommagé."
        ) from exc

    blocks = []
    page_texts = []

    try:
        if document.page_count > MAX_PDF_PAGES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Le PDF contient {document.page_count} pages. "
                    f"La limite est de {MAX_PDF_PAGES} pages."
                ),
            )

        # OCRust travaille directement sur le PDF.
        # Chaque page est traitée séparément pour limiter la mémoire.
        with tempfile.TemporaryDirectory() as temp_dir:

            pdf_path = Path(temp_dir) / "document.pdf"
            pdf_path.write_bytes(data)

            for page_number in range(document.page_count):

                try:
                    ocr_document = ocr_engine.scan(
                        str(pdf_path),
                        pages=[page_number]
                    )

                    page_blocks = convert_ocr_result(ocr_document)

                    for block in page_blocks:
                        block["page"] = page_number + 1

                    blocks.extend(page_blocks)

                    page_texts.append(
                        "\n".join(block["text"] for block in page_blocks)
                    )

                except Exception as exc:
                    logger.exception(
                        "Erreur OCR sur la page %s",
                        page_number + 1
                    )
                    raise RuntimeError(
                        f"Erreur OCR sur la page {page_number + 1} : {exc}"
                    ) from exc

    finally:
        document.close()

    return (
        "\n\n".join(text for text in page_texts if text),
        blocks,
        len(page_texts)
    )


@app.post("/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    model: str = Form("paddle-fr"),
    prompt: str = Form(""),
    example: str = Form("none"),
) -> dict[str, Any]:

    del prompt, example

    filename = Path(file.filename or "document").name
    extension = Path(filename).suffix.lower()

    allowed = {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".txt",
        ".md"
    }

    if extension not in allowed:
        raise HTTPException(
            status_code=415,
            detail="Format non pris en charge."
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Le fichier envoyé est vide."
        )

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Le fichier dépasse la limite de "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} Mo."
            )
        )

    # Traitement des fichiers texte sans OCR.
    if extension in {".txt", ".md"}:

        try:
            text = content.decode("utf-8-sig")

        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")

        return {
            "filename": filename,
            "text": text,
            "blocks": [],
            "pages_processed": 1,
            "engine": "texte"
        }

    if model == "manual":
        raise HTTPException(
            status_code=400,
            detail="Le mode manuel est réservé aux fichiers TXT et MD."
        )

    # Identifiants conservés pour la compatibilité avec l'interface.
    language_by_model = {
        "paddle-fr": "fr",
        "paddle-en": "en",
        "paddle-fr-en": "fr,en",
        "tesseract-fr": "fr",
        "tesseract-en": "en",
        "tesseract-fr-en": "fr,en",
    }

    if model not in language_by_model:
        raise HTTPException(
            status_code=400,
            detail="Modèle de traitement inconnu."
        )

    language = language_by_model[model]

    try:

        if extension == ".pdf":

            text, blocks, pages = await run_in_threadpool(
                extract_from_pdf,
                content,
                language
            )

        else:

            text, blocks = await run_in_threadpool(
                extract_from_image,
                content,
                language
            )

            pages = 1

    except RuntimeError as exc:

        logger.exception("Moteur OCR indisponible")

        raise HTTPException(
            status_code=503,
            detail=str(exc)
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:

        logger.exception("Erreur pendant l'extraction OCR")

        raise HTTPException(
            status_code=500,
            detail="Une erreur est survenue pendant l'extraction OCR."
        ) from exc

    return {
        "filename": filename,
        "text": text,
        "blocks": blocks,
        "pages_processed": pages,
        "engine": "OCRust"
    }
