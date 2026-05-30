DOCKER ?= docker
IMAGE ?= md-convert
BASE_IMAGE ?= md-convert-base:latest
PORT ?= 8000

.PHONY: help build-base build-app build run run-hardened test

help:
	@echo "Targets:"
	@echo "  make build-base     Build heavy TeX base image"
	@echo "  make build-app      Build app image from BASE_IMAGE"
	@echo "  make build          Build base and app images"
	@echo "  make run            Run app container on PORT"
	@echo "  make run-hardened   Run app with hardened container flags"
	@echo "  make test           Run Python tests"

build-base:
	$(DOCKER) build -f Dockerfile.base -t $(BASE_IMAGE) .

build-app:
	$(DOCKER) build --build-arg BASE_IMAGE=$(BASE_IMAGE) -t $(IMAGE) .

build: build-base build-app

run:
	$(DOCKER) run --rm -p $(PORT):8000 $(IMAGE)

run-hardened:
	$(DOCKER) run --rm -p $(PORT):8000 \
		--read-only \
		--tmpfs /app/tmp:rw,noexec,nosuid,size=256m \
		--cap-drop ALL \
		--security-opt no-new-privileges \
		$(IMAGE)

test:
	source .venv/bin/activate && python -m pytest -q
