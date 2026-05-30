DOCKER ?= docker
IMAGE ?= md-convert
BASE_IMAGE ?= md-convert-base:latest
IMAGE_NO_PDF ?= md-convert-no-pdf
BASE_IMAGE_NO_PDF ?= md-convert-base-no-pdf:latest
PORT ?= 8000

.PHONY: help build-base build-app build build-base-no-pdf build-app-no-pdf build-no-pdf run run-hardened run-no-pdf test

help:
	@echo "Targets:"
	@echo "  make build-base     Build heavy TeX base image"
	@echo "  make build-app      Build app image from BASE_IMAGE"
	@echo "  make build          Build base and app images"
	@echo "  make build-no-pdf   Build app image without PDF support (smaller)"
	@echo "  make run            Run app container on PORT"
	@echo "  make run-hardened   Run app with hardened container flags"
	@echo "  make run-no-pdf     Run no-PDF app container on PORT"
	@echo "  make test           Run Python tests"

build-base:
	$(DOCKER) build -f Dockerfile.base -t $(BASE_IMAGE) .

build-base-no-pdf:
	$(DOCKER) build -f Dockerfile.base --build-arg ENABLE_PDF=false -t $(BASE_IMAGE_NO_PDF) .

build-app:
	$(DOCKER) build --build-arg BASE_IMAGE=$(BASE_IMAGE) -t $(IMAGE) .

build-app-no-pdf:
	$(DOCKER) build --build-arg BASE_IMAGE=$(BASE_IMAGE_NO_PDF) -t $(IMAGE_NO_PDF) .

build: build-base build-app

build-no-pdf: build-base-no-pdf build-app-no-pdf

run:
	$(DOCKER) run --rm -p $(PORT):8000 $(IMAGE)

run-no-pdf:
	$(DOCKER) run --rm -p $(PORT):8000 $(IMAGE_NO_PDF)

run-hardened:
	$(DOCKER) run --rm -p $(PORT):8000 \
		--read-only \
		--tmpfs /app/tmp:rw,noexec,nosuid,size=256m \
		--cap-drop ALL \
		--security-opt no-new-privileges \
		$(IMAGE)

test:
	source .venv/bin/activate && python -m pytest -q
