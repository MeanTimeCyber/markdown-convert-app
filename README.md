# Overview

Simple web app for converting markdown to DOCX or PDF using pandoc. Built using GitHub Copilot. As sensibly as possible, but only suitable for running locally or on a private network.

## What this includes

- FastAPI backend that wraps pandoc
- Single-page upload form
- Upload support for markdown and optional asset files (images, etc.)
- Optional ZIP package upload containing markdown + assets
- Relative path preservation for uploaded asset folders
- Docker setup with pandoc and full TeX Live for broad PDF compatibility

## Run with Docker

1. Build TeX base image (slow, done rarely):

   docker build -f Dockerfile.base -t md-convert-base:latest .

2. Build app image (fast, done frequently):

   docker build -t md-convert .

3. Run container:

   docker run --rm -p 8000:8000 md-convert

4. Production-style hardened run:

   docker run --rm -p 8000:8000 \
     --read-only \
     --tmpfs /app/tmp:rw,noexec,nosuid,size=256m \
     --cap-drop ALL \
     --security-opt no-new-privileges \
     md-convert

5. Run with a template document:

   docker run --rm -p 8000:8000 \
     -v /path/to/your/template.docx:/template.docx \
     -e TEMPLATE_DOC_PATH=/template.docx \
     md-convert

6. Open browser:

   http://localhost:8000

## Makefile Shortcuts

- Build TeX base image: make build-base
- Build app image: make build-app
- Build both images: make build
- Run container: make run
- Run hardened container: make run-hardened
- Run tests: make test

## Run locally

1. Install pandoc and a PDF engine (xelatex recommended).
2. Create virtual environment and install Python dependencies:

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

3. Start server:

   uvicorn app.main:app --reload

   Or with a template document:

   TEMPLATE_DOC_PATH=/path/to/template.docx uvicorn app.main:app --reload

4. Open:

   http://localhost:8000

## Notes

- Main markdown file must end with .md, .markdown, or .mdown.
- You can upload either a direct markdown file, a ZIP package, or both.
- Per-file upload limit: 20 MB.
- Total upload limit per request: 100 MB.
- Extracted ZIP content limit per request: 200 MB.
- ZIP controls: max 2000 entries, max depth 12, symlinks blocked, high compression ratio blocked.
- PDF conversion calls pandoc with --pdf-engine xelatex by default.
- You can override the PDF engine with the PDF_ENGINE environment variable.
- **Template documents (optional)**: Specify a Word document template to style DOCX output. Set the TEMPLATE_DOC_PATH environment variable to the path of a .docx template file. Users can then enable/disable template application via a checkbox in the UI. Templates are only applied to DOCX output, not PDF.
  - To create a custom reference document: `pandoc -o custom-reference.docx --print-default-data-file reference.docx`
  - Then edit the generated `custom-reference.docx` with your preferred styles, colors, fonts, etc.
  - Pass it to the app via `TEMPLATE_DOC_PATH=/path/to/custom-reference.docx`
- Docker image includes texlive-full to avoid missing LaTeX/font packages for PDF.
- texlive-full significantly increases image build time and image size.
- Use Dockerfile.base for rare TeX rebuilds and Dockerfile for fast app rebuilds.
- You can override base image at build time: docker build --build-arg BASE_IMAGE=<tag> -t md-convert .
- Security middleware: rate limiting, request IDs, and security headers are enabled by default.

## Logging

The app emits structured JSON logs and can write to a rotating log file.

Environment variables:

- `LOG_LEVEL` (default: `INFO`)
- `LOG_TO_FILE` (default: `true`)
- `LOG_FILE_PATH` (default: `/tmp/md-convert.log`)
- `LOG_FILE_MAX_BYTES` (default: `10485760`, 10 MB)
- `LOG_FILE_BACKUP_COUNT` (default: `5`)

Example (local):

`LOG_TO_FILE=true LOG_FILE_PATH=./logs/md-convert.log uvicorn app.main:app --reload`

Example (Docker with persisted logs):

`docker run --rm -p 8000:8000 -v $(pwd)/logs:/logs -e LOG_FILE_PATH=/logs/md-convert.log md-convert`

Python logging best-practice details used in this app:

- Centralized logger setup at startup (single source of truth).
- Structured JSON log lines for machine parsing and search.
- Rotation with bounded file size and backups to avoid unbounded disk growth.
- Simultaneous console + file handlers so logs work in containers and local dev.
- Named application logger (`md_convert`) with propagation disabled to avoid duplicate lines.
- Request correlation fields (`request_id`, method/path, latency, status) included in each request log.

## Optional Build Cache

Use BuildKit cache mounts/registry cache in CI for faster repeat builds.

Example with buildx local cache:

docker buildx build \
   --cache-from type=local,src=.buildx-cache \
   --cache-to type=local,dest=.buildx-cache-new,mode=max \
   -f Dockerfile.base -t md-convert-base:latest .

## Run tests

python -m pytest -q
