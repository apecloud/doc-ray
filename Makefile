# ==============================================================================
# Variables
# ==============================================================================
# Default image name. Override with: make build IMAGE_NAME=yourimage/yourname
IMAGE_NAME ?= apecloud/doc-ray
# Default image tag. Override with: make build IMAGE_TAG=yourtag
IMAGE_TAG  ?= latest
# Optional registry prefix. E.g., "docker.io/" or "your-registry.com/"
# Override with: make build REGISTRY=myregistry.com/
REGISTRY   ?=
# Platforms for multi-arch build (comma-separated)
PLATFORMS  ?= linux/amd64,linux/arm64
# Container name for standalone run
CONTAINER_NAME ?= doc-ray-standalone

# Construct the full image name using registry, image name, and tag
FULL_IMAGE_NAME = $(REGISTRY)$(IMAGE_NAME)

# Python interpreter for running scripts
PYTHON = python3

# ==============================================================================
# Helper Targets
# ==============================================================================
.PHONY: help
help:
	@echo "Makefile for the doc-ray project"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Common Targets:"
	@echo "  help                         Show this help message."
	@echo "  download-models              Download models required by MinerU using the script."
	@echo "  build                        Build the Docker image for the host architecture."
	@echo "  build-and-push-multiarch     Build multi-architecture Docker images (for $(PLATFORMS)) and push them."
	@echo "  run-standalone               Run the application in a standalone Docker container."
	@echo "  stop-standalone              Stop the standalone Docker container."
	@echo "  rm-standalone                Remove the standalone Docker container."
	@echo "  logs-standalone              Follow logs of the standalone Docker container."
	@echo "  clean                        Stop and remove the standalone container."
	@echo ""
	@echo "Variables (can be overridden on the command line):"
	@echo "  IMAGE_NAME     (default: $(IMAGE_NAME))       - Name of the Docker image."
	@echo "  IMAGE_TAG      (default: $(IMAGE_TAG))        - Tag for the Docker image."
	@echo "  REGISTRY       (default: none)                - Docker registry prefix (e.g., 'your.registry.com/')."
	@echo "  PLATFORMS      (default: $(PLATFORMS)) - Comma-separated platforms for multi-arch builds."
	@echo "  CONTAINER_NAME (default: $(CONTAINER_NAME))   - Name for the standalone Docker container."

# ==============================================================================
# Development Tasks
# ==============================================================================
.PHONY: download-models
download-models:
	@echo ">>> Downloading models required by MinerU..."
	$(PYTHON) ./scripts/prepare_for_mineru.py
	@echo ">>> Model download script finished."

# ==============================================================================
# Docker Image Management
# ==============================================================================
.PHONY: build
build:
	@echo ">>> Building Docker image for host architecture: $(FULL_IMAGE_NAME):$(IMAGE_TAG)"
	docker build -t $(FULL_IMAGE_NAME):models-$(IMAGE_TAG) \
		-f build/Dockerfile-models .

	docker build -t $(FULL_IMAGE_NAME):venv-$(IMAGE_TAG) \
		-f build/Dockerfile-venv .

	docker build -t $(FULL_IMAGE_NAME):base-$(IMAGE_TAG) \
		-f build/Dockerfile-base .

	docker build -t $(FULL_IMAGE_NAME):$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(FULL_IMAGE_NAME):base-$(IMAGE_TAG) \
		--build-arg VENV_IMAGE=$(FULL_IMAGE_NAME):venv-$(IMAGE_TAG) \
		--build-arg MODELS_IMAGE=$(FULL_IMAGE_NAME):models-$(IMAGE_TAG) \
		--build-arg FINAL_BASE=base-with-models \
		-f build/Dockerfile .

.PHONY: build-and-push-multiarch
build-and-push-multiarch:
	@echo ">>> Building and pushing multi-architecture Docker image: $(FULL_IMAGE_NAME):$(IMAGE_TAG)"
	@echo ">>> Target platforms: $(PLATFORMS)"
	@echo ">>> Note: Ensure Docker Buildx is configured and your builder supports multi-platform builds."

	docker buildx create --name doc-ray-builder 2>/dev/null || true
	docker buildx use doc-ray-builder

	docker buildx build --platform $(PLATFORMS) -t $(FULL_IMAGE_NAME):models-$(IMAGE_TAG) \
		--push -f build/Dockerfile-models .

	docker buildx build --platform $(PLATFORMS) -t $(FULL_IMAGE_NAME):venv-$(IMAGE_TAG) \
		--push -f build/Dockerfile-venv .

	docker buildx build --platform $(PLATFORMS) -t $(FULL_IMAGE_NAME):base-$(IMAGE_TAG) \
		--push -f build/Dockerfile-base .

	@# The image for KubeRay doesn't contain model files.
	docker buildx build --platform $(PLATFORMS) -t $(FULL_IMAGE_NAME):kuberay-$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(FULL_IMAGE_NAME):base-$(IMAGE_TAG) \
		--build-arg VENV_IMAGE=$(FULL_IMAGE_NAME):venv-$(IMAGE_TAG) \
		--build-arg MODELS_IMAGE=$(FULL_IMAGE_NAME):models-$(IMAGE_TAG) \
		--push -f build/Dockerfile .

	docker buildx build --platform $(PLATFORMS) -t $(FULL_IMAGE_NAME):$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(FULL_IMAGE_NAME):base-$(IMAGE_TAG) \
		--build-arg VENV_IMAGE=$(FULL_IMAGE_NAME):venv-$(IMAGE_TAG) \
		--build-arg MODELS_IMAGE=$(FULL_IMAGE_NAME):models-$(IMAGE_TAG) \
		--build-arg FINAL_BASE=base-with-models \
		--push -f build/Dockerfile .

# ==============================================================================
# Standalone Docker Container Management
# ==============================================================================
.PHONY: run-standalone
run-standalone:
	@echo ">>> Attempting to run standalone Docker container '$(CONTAINER_NAME)' from image $(FULL_IMAGE_NAME):$(IMAGE_TAG)..."
	-docker stop $(CONTAINER_NAME) > /dev/null 2>&1 || true
	-docker rm $(CONTAINER_NAME) > /dev/null 2>&1 || true
	@echo ">>> Starting container '$(CONTAINER_NAME)'..."
	@# Check if the current OS is macOS (Darwin)
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		echo ">>> Detected macOS, running without --gpus=all"; \
		docker run -d \
			-p 8639:8639 \
			-p 8265:8265 \
			--name $(CONTAINER_NAME) \
			$(FULL_IMAGE_NAME):$(IMAGE_TAG); \
	else \
		echo ">>> Detected non-macOS, running with --gpus=all"; \
		docker run -d \
			-p 8639:8639 \
			-p 8265:8265 \
			--gpus=all \
			--name $(CONTAINER_NAME) \
			$(FULL_IMAGE_NAME):$(IMAGE_TAG); \
	fi

	@echo ">>> Container $(CONTAINER_NAME) started."
	@echo ">>> Ray Serve should be accessible on http://localhost:8639"
	@echo ">>> Ray Dashboard should be accessible on http://localhost:8265"

.PHONY: stop-standalone
stop-standalone:
	@echo ">>> Stopping standalone Docker container '$(CONTAINER_NAME)'..."
	docker stop $(CONTAINER_NAME)

.PHONY: rm-standalone
rm-standalone:
	@echo ">>> Removing standalone Docker container '$(CONTAINER_NAME)'..."
	docker rm $(CONTAINER_NAME)

.PHONY: logs-standalone
logs-standalone:
	@echo ">>> Following logs for standalone Docker container '$(CONTAINER_NAME)'..."
	docker logs -f $(CONTAINER_NAME)

.PHONY: clean
clean:
	@echo ">>> Attempting to stop and remove standalone Docker container '$(CONTAINER_NAME)'..."
	-docker stop $(CONTAINER_NAME) > /dev/null 2>&1 || true
	-docker rm $(CONTAINER_NAME) > /dev/null 2>&1 || true
	@echo ">>> Standalone container cleanup finished."
