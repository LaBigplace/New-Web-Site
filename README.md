# Atelier — reconstruction

Reconstruction de l'interface Atelier à partir des fichiers HTML/CSS/JS de référence, avec une API FastAPI réelle.

## Fonctionnalités

- Import de plusieurs PDF, images (PNG/JPG/JPEG/WEBP/GIF) et fichiers texte (TXT/MD).
- Prévisualisation locale du document et sélection/suppression dans la liste.
- Extraction OCR des PDF et images via Tesseract OCR sur le serveur.
- Lecture directe des fichiers TXT/MD sans OCR.
- Texte extrait éditable, compteur de mots, copie et export TXT.
- Prompts prédéfinis et champ d'instructions conservés dans l'interface.
- API avec validation de format/taille, limite de pages PDF et erreurs JSON explicites.
- Chargement du moteur OCR à la première demande, puis réutilisation du moteur en mémoire.

## Lancer en local

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn api:app --reload
```

Ouvrir ensuite http://127.0.0.1:8000.

## Déploiement Render

Le fichier `render.yaml` contient la configuration du service gratuit. Le point d'entrée est `api:app` et le contrôle de santé est `/api/health`.

## API

- `GET /api/health` : état du service.
- `POST /api/extract` : multipart avec `file`, `model`, `prompt`, `example`; retourne `filename`, `text`, `blocks`, `pages_processed`, `engine`.
- `GET /Verification/` : route de compatibilité ancienne.

## Limites à connaître

- Le moteur de cette reconstruction est Tesseract OCR. Les anciennes options de démonstration `dots.mocr` et `dots.ocr` ont été remplacées par des choix explicites Tesseract OCR français, anglais ou bilingue ; ce projet ne prétend pas appeler ces modèles dots.
- L'OCR de documents complexes, de formules mathématiques et de pages multicolonnes peut nécessiter un moteur spécialisé supplémentaire.
- Tesseract et ses données linguistiques sont installés au déploiement à partir de `apt.txt`.
- Render gratuit dispose de ressources mémoire/CPU limitées. Tesseract est nettement plus léger que les grands modèles OCR basés sur des réseaux de neurones, mais un PDF volumineux ou plusieurs traitements simultanés peuvent tout de même être lents.
- Le champ de prompt reste éditable et transmis à l'API, mais il ne pilote pas un LLM : Tesseract effectue une reconnaissance de texte, pas une interprétation sémantique du prompt.
