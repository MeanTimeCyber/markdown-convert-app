# md-convert

Simple web app for converting markdown to DOCX or PDF using pandoc.

## What this includes

- FastAPI backend that wraps pandoc
- Single-page upload form
- Upload support for markdown and optional asset files (images, etc.)
- Optional ZIP package upload containing markdown + assets
- Relative path preservation for uploaded asset folders
- Docker setup with pandoc and tectonic for PDF output

## Run with Docker

1. Build image:

   docker build -t md-convert .

2. Run container:

   docker run --rm -p 8000:8000 md-convert

3. Open browser:

   http://localhost:8000

## Run locally

1. Install pandoc and a PDF engine (tectonic recommended).
2. Create virtual environment and install Python dependencies:

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

3. Start server:

   uvicorn app.main:app --reload

4. Open:

   http://localhost:8000

## Notes

- Main markdown file must end with .md, .markdown, or .mdown.
- You can upload either a direct markdown file, a ZIP package, or both.
- Per-file upload limit: 20 MB.
- Total upload limit per request: 100 MB.
- Extracted ZIP content limit per request: 200 MB.
- PDF conversion calls pandoc with --pdf-engine tectonic.

## Run tests

python -m pytest -q
