
from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from xml.sax.saxutils import escape

import fitz
import ocrust
import torch

from transformers import (
    AutoTokenizer,
    AutoModelForSeq2SeqLM
)

from PIL import Image, UnidentifiedImageError

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from pypdf import PdfWriter


# ==========================================
# CONFIGURATION
# ==========================================

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("atelier")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "25"))

LANGUE_ORIGINE = "fr"
LANGUE_CIBLE = "en"

RESOLUTION = 2.0

NOM_MODELE = "Helsinki-NLP/opus-mt-fr-en"


# ==========================================
# INITIALISATION OCR
# ==========================================

ocr_engine = ocrust.Ocr(
    lang="fr",
    page_workers=1,
    pdf_dpi=150,
    det_limit_side=640,
    rec_batch_size=1,
    word_boxes=False,
)


# ==========================================
# TRADUCTEUR
# ==========================================

class Traducteur:

    def __init__(self):

        self.device = torch.device("cpu")

        logger.info("Chargement du modèle : %s", NOM_MODELE)

        self.tokenizer = AutoTokenizer.from_pretrained(
            NOM_MODELE
        )

        self.modele = AutoModelForSeq2SeqLM.from_pretrained(
            NOM_MODELE
        )

        self.modele.to(self.device)
        self.modele.eval()

        logger.info("Modèle de traduction chargé sur CPU")

    def traduire_blocs(self, blocs, taille_lot=8):

        textes = [
            bloc["texte"]
            for bloc in blocs
        ]

        traductions = []

        for debut in range(0, len(textes), taille_lot):

            lot = textes[debut:debut + taille_lot]

            entrees = self.tokenizer(
                lot,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512
            )

            entrees = {
                cle: valeur.to(self.device)
                for cle, valeur in entrees.items()
            }

            with torch.inference_mode():

                resultats = self.modele.generate(
                    **entrees,
                    num_beams=4,
                    max_new_tokens=256
                )

            traductions.extend(
                self.tokenizer.batch_decode(
                    resultats,
                    skip_special_tokens=True
                )
            )

        blocs_traduits = []

        for bloc, traduction in zip(blocs, traductions):

            nouveau_bloc = bloc.copy()
            nouveau_bloc["texte_traduit"] = traduction

            blocs_traduits.append(nouveau_bloc)

        return blocs_traduits


# Le modèle est chargé uniquement à la première traduction.
# Il est ensuite réutilisé pour les requêtes suivantes.

traducteur = None
traducteur_lock = threading.Lock()


def obtenir_traducteur():

    global traducteur

    if traducteur is None:

        with traducteur_lock:

            if traducteur is None:
                traducteur = Traducteur()

    return traducteur


# ==========================================
# CONSTRUCTEUR PDF
# ==========================================

class PDFBuilder:

    def __init__(self, echelle=2.0):

        self.echelle = echelle

    def construire(
        self,
        chemin_image,
        blocs,
        chemin_sortie
    ):

        chemin_image = Path(chemin_image)
        chemin_sortie = Path(chemin_sortie)

        chemin_sortie.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        image = Image.open(chemin_image).convert("RGB")

        largeur_px, hauteur_px = image.size

        largeur = largeur_px / self.echelle
        hauteur = hauteur_px / self.echelle

        document = canvas.Canvas(
            str(chemin_sortie),
            pagesize=(largeur, hauteur)
        )

        document.drawImage(
            ImageReader(image),
            0,
            0,
            width=largeur,
            height=hauteur
        )

        for bloc in blocs:

            texte = bloc.get("texte_traduit", "")

            if not texte.strip():
                continue

            x1, y1, x2, y2 = bloc["position"]

            x1 = max(0, x1 / self.echelle)
            x2 = min(largeur, x2 / self.echelle)

            y1 = max(0, y1 / self.echelle)
            y2 = min(hauteur, y2 / self.echelle)

            largeur_bloc = x2 - x1
            hauteur_bloc = y2 - y1

            if largeur_bloc <= 0 or hauteur_bloc <= 0:
                continue

            # Masquage de l'ancien texte.

            document.setFillColorRGB(1, 1, 1)

            document.rect(
                x1,
                hauteur - y2,
                largeur_bloc,
                hauteur_bloc,
                fill=1,
                stroke=0
            )

            taille = min(12, hauteur_bloc * 0.75)

            paragraphe = None

            while taille >= 4:

                style = ParagraphStyle(
                    name="Traduction",
                    fontName="Helvetica",
                    fontSize=taille,
                    leading=taille * 1.1,
                    alignment=TA_LEFT,
                    textColor="#000000"
                )

                paragraphe = Paragraph(
                    escape(texte),
                    style
                )

                _, hauteur_texte = paragraphe.wrap(
                    largeur_bloc,
                    hauteur_bloc
                )

                if hauteur_texte <= hauteur_bloc:
                    break

                taille -= 0.5

            if paragraphe is None:
                continue

            _, hauteur_texte = paragraphe.wrap(
                largeur_bloc,
                hauteur_bloc
            )

            if hauteur_texte > hauteur_bloc:
                continue

            position_y = (
                hauteur - y2
                + (hauteur_bloc - hauteur_texte) / 2
            )

            paragraphe.drawOn(
                document,
                x1,
                position_y
            )

        document.save()
        image.close()


constructeur = PDFBuilder(echelle=RESOLUTION)


# ==========================================
# OCR
# ==========================================

def reconnaitre_image(image: Image.Image):

    document = ocr_engine.scan(
        image,
        name="page"
    )

    blocs = []

    for ligne in document.lines:

        texte = str(ligne.text).strip()

        if not texte:
            continue

        x1, y1, x2, y2 = ligne.box.as_tuple()

        blocs.append({
            "texte": texte,
            "position": [
                int(x1),
                int(y1),
                int(x2),
                int(y2)
            ],
            "confidence": round(
                float(ligne.confidence),
                3
            )
        })

    return blocs


# ==========================================
# TRAITEMENT D'UNE PAGE
# ==========================================

def traiter_page(image: Image.Image, numero: int, dossier_temp: Path):

    logger.info("Traitement de la page %s", numero)

    # 1. OCR

    blocs = reconnaitre_image(image)

    logger.info("%s blocs détectés", len(blocs))

    # 2. Traduction

    moteur = obtenir_traducteur()

    with traducteur_lock:
        blocs_traduits = moteur.traduire_blocs(blocs)

    # 3. Reconstruction PDF

    chemin_image = dossier_temp / f"page_{numero}.png"
    chemin_pdf = dossier_temp / f"resultat_{numero}.pdf"

    image.save(chemin_image, format="PNG")

    constructeur.construire(
        chemin_image,
        blocs_traduits,
        chemin_pdf
    )

    return chemin_pdf


# ==========================================
# EXTRACTION ET TRADUCTION PDF
# ==========================================

def traiter_pdf(contenu: bytes):

    document = fitz.open(
        stream=contenu,
        filetype="pdf"
    )

    assembleur = PdfWriter()

    try:

        nombre_pages = document.page_count

        if nombre_pages > MAX_PDF_PAGES:

            raise HTTPException(
                status_code=413,
                detail=(
                    f"Le PDF contient {nombre_pages} pages. "
                    f"La limite est de {MAX_PDF_PAGES} pages."
                )
            )

        logger.info("Nombre de pages : %s", nombre_pages)

        with TemporaryDirectory() as dossier:

            dossier_temp = Path(dossier)

            for numero, page in enumerate(document, start=1):

                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(
                        RESOLUTION,
                        RESOLUTION
                    ),
                    alpha=False
                )

                image = Image.frombytes(
                    "RGB",
                    (pixmap.width, pixmap.height),
                    pixmap.samples
                )

                try:

                    chemin_pdf = traiter_page(
                        image,
                        numero,
                        dossier_temp
                    )

                    assembleur.append(str(chemin_pdf))

                finally:
                    image.close()

            sortie = io.BytesIO()

            assembleur.write(sortie)

            return sortie.getvalue()

    finally:
        document.close()


# ==========================================
# TRAITEMENT IMAGE
# ==========================================

def traiter_image(contenu: bytes):

    try:

        with Image.open(io.BytesIO(contenu)) as image:

            image.load()
            image = image.convert("RGB")

            with TemporaryDirectory() as dossier:

                chemin_pdf = traiter_page(
                    image,
                    1,
                    Path(dossier)
                )

                return chemin_pdf.read_bytes()

    except (UnidentifiedImageError, OSError) as exc:

        raise HTTPException(
            status_code=400,
            detail="Le fichier image est illisible ou endommagé."
        ) from exc


# ==========================================
# FASTAPI
# ==========================================

app = FastAPI(
    title="Atelier — API de traitement documentaire",
    version="1.0.0"
)

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static"
)


@app.get("/", include_in_schema=False)
async def home():

    return FileResponse(
        STATIC_DIR / "index.html"
    )


@app.get("/api/health")
async def health():

    return {
        "status": "ok",
        "service": "atelier"
    }


@app.get("/Verification/", include_in_schema=False)
async def legacy_verification():

    return {"status": "ok"}


# ==========================================
# ROUTE PRINCIPALE
# ==========================================

@app.post("/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    model: str = Form("paddle-fr"),
    prompt: str = Form(""),
    example: str = Form("none"),
):

    del prompt, example

    filename = Path(
        file.filename or "document"
    ).name

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

    content = await file.read(
        MAX_UPLOAD_BYTES + 1
    )

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

    # Fichiers texte : conservation du comportement précédent.

    if extension in {".txt", ".md"}:

        try:
            texte = content.decode("utf-8-sig")

        except UnicodeDecodeError:
            texte = content.decode(
                "utf-8",
                errors="replace"
            )

        return {
            "filename": filename,
            "text": texte,
            "blocks": [],
            "pages_processed": 1,
            "engine": "texte"
        }

    if model == "manual":

        raise HTTPException(
            status_code=400,
            detail="Le mode manuel est réservé aux fichiers TXT et MD."
        )

    modeles_acceptes = {
        "paddle-fr",
        "paddle-en",
        "paddle-fr-en",
        "tesseract-fr",
        "tesseract-en",
        "tesseract-fr-en"
    }

    if model not in modeles_acceptes:

        raise HTTPException(
            status_code=400,
            detail="Modèle de traitement inconnu."
        )

    try:

        if extension == ".pdf":

            pdf_traduit = await run_in_threadpool(
                traiter_pdf,
                content
            )

        else:

            pdf_traduit = await run_in_threadpool(
                traiter_image,
                content
            )

        nom_sortie = f"{Path(filename).stem}_Output.pdf"

        return StreamingResponse(
            io.BytesIO(pdf_traduit),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{nom_sortie}"'
                )
            }
        )

    except HTTPException:
        raise

    except Exception as exc:

        logger.exception(
            "Erreur pendant le traitement du document"
        )

        raise HTTPException(
            status_code=500,
            detail=f"Erreur pendant le traitement : {exc}"
        ) from exc
