from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any

import fitz
import pytesseract
from pytesseract import Output
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("atelier")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "25"))
PDF_RENDER_SCALE = float(os.getenv("PDF_RENDER_SCALE", "1.5"))

app = FastAPI(title="Atelier — API de traitement documentaire", version="1.0.0")
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


def recognize_image(image: Image.Image, language: str) -> list[dict[str, Any]]:
    """Reconnaissance OCR Tesseract, légère et adaptée à un petit serveur CPU."""
    try:
        data = pytesseract.image_to_data(
            image.convert("RGB"),
            lang=language,
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract n'est pas installé sur le serveur. Vérifie apt.txt et le déploiement Render."
        ) from exc
    except pytesseract.TesseractError as exc:
        raise RuntimeError(
            f"La langue OCR '{language}' n'est pas installée sur le serveur."
        ) from exc

    lines: dict[tuple[int, int, int], list[int]] = {}
    for index, raw_text in enumerate(data.get("text", [])):
        word = str(raw_text).strip()
        if not word:
            continue
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        lines.setdefault(key, []).append(index)

    blocks: list[dict[str, Any]] = []
    for indices in lines.values():
        words = [str(data["text"][i]).strip() for i in indices]
        confidences = [float(data["conf"][i]) for i in indices if float(data["conf"][i]) >= 0]
        x1 = min(int(data["left"][i]) for i in indices)
        y1 = min(int(data["top"][i]) for i in indices)
        x2 = max(int(data["left"][i]) + int(data["width"][i]) for i in indices)
        y2 = max(int(data["top"][i]) + int(data["height"][i]) for i in indices)
        blocks.append({
            "text": " ".join(words),
            "confidence": round(sum(confidences) / len(confidences) / 100, 3) if confidences else 0.0,
            "box": [x1, y1, x2, y2],
        })

    blocks.sort(key=lambda block: (block["box"][1], block["box"][0]))
    return blocks


def extract_from_image(data: bytes, language: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            blocks = recognize_image(image, language)
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Le fichier image est illisible ou endommagé.") from exc
    return "\n".join(block["text"] for block in blocks), blocks


def extract_from_pdf(data: bytes, language: str) -> tuple[str, list[dict[str, Any]], int]:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Le PDF est invalide ou endommagé.") from exc

    blocks: list[dict[str, Any]] = []
    page_texts: list[str] = []
    try:
        if document.page_count > MAX_PDF_PAGES:
            raise HTTPException(
                status_code=413,
                detail=f"Le PDF contient {document.page_count} pages. La limite est de {MAX_PDF_PAGES} pages.",
            )
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            page_blocks = recognize_image(image, language)
            image.close()
            for block in page_blocks:
                block["page"] = page_number
            blocks.extend(page_blocks)
            page_texts.append("\n".join(block["text"] for block in page_blocks))
    finally:
        document.close()

    return "\n\n".join(text for text in page_texts if text), blocks, len(page_texts)


@app.post("/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    model: str = Form("paddle-fr"),
    prompt: str = Form(""),
    example: str = Form("none"),
) -> dict[str, Any]:
    del prompt, example  # Les instructions sont conservées côté interface ; OCR reste déterministe.
    filename = Path(file.filename or "document").name
    extension = Path(filename).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md"}
    if extension not in allowed:
        raise HTTPException(status_code=415, detail="Format non pris en charge.")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="Le fichier envoyé est vide.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Le fichier dépasse la limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo.")

    if extension in {".txt", ".md"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        return {"filename": filename, "text": text, "blocks": [], "pages_processed": 1, "engine": "texte"}

    if model == "manual":
        raise HTTPException(status_code=400, detail="Le mode manuel est réservé aux fichiers TXT et MD.")
    language_by_model = {
        "tesseract-fr": "fra",
        "tesseract-en": "eng",
        "tesseract-fr-en": "fra+eng",
    }
    if model not in language_by_model:
        raise HTTPException(status_code=400, detail="Modèle de traitement inconnu.")

    language = language_by_model[model]

    try:
        if extension == ".pdf":
            text, blocks, pages = await run_in_threadpool(extract_from_pdf, content, language)
        else:
            text, blocks = await run_in_threadpool(extract_from_image, content, language)
            pages = 1
    except RuntimeError as exc:
        logger.exception("Moteur OCR indisponible")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur pendant l'extraction OCR")
        raise HTTPException(status_code=500, detail="Une erreur est survenue pendant l'extraction OCR.") from exc

    return {
        "filename": filename,
        "text": text,
        "blocks": blocks,
        "pages_processed": pages,
        "engine": "Tesseract OCR",
    }
