# Use an official Python runtime as a parent image
# Stage 1: Install deps
FROM python:3.11-slim AS install-deps
ENV PYTHONUNBUFFERED=1

# Install uv globally
# uv will be used in the final stage to install project dependencies.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy the pyproject.toml and uv.lock (if it exists) first
# This allows Docker to leverage caching for dependencies if they haven't changed
COPY pyproject.toml ./
COPY uv.lock ./

# Install dependencies using uv
# --no-cache to reduce image size
RUN uv sync --no-cache --prerelease=allow

# Stage 2: Model Downloader
# This stage is dedicated to downloading models. It leverages Docker caching:
# if the script hasn't changed, this stage will be cached, and models won't be re-downloaded.
FROM python:3.11-slim AS model-downloader
ENV PYTHONUNBUFFERED=1

# Define whether to use ModelScope or Hugging Face models.
# This can be overridden at build time via --build-arg USE_MODELSCOPE=1
ARG USE_MODELSCOPE="0"
ENV USE_MODELSCOPE=${USE_MODELSCOPE}

# Define the target directory for Hugging Face models/cache.
# This can be overridden at build time via --build-arg HF_MODELS_CACHE_PATH=...
ARG HF_MODELS_CACHE_PATH="/app/models_cache"
ENV HF_HOME=${HF_MODELS_CACHE_PATH} \
    HUGGINGFACE_HUB_CACHE=${HF_MODELS_CACHE_PATH} \
    MODELSCOPE_CACHE=${HF_MODELS_CACHE_PATH}
RUN mkdir -p ${HF_HOME} # Ensure the cache directory exists

WORKDIR /app
RUN pip install --no-cache-dir huggingface_hub modelscope
COPY scripts/prepare_for_mineru.py ./scripts/prepare_for_mineru.py
RUN python ./scripts/prepare_for_mineru.py

# Stage 3: Final Application Image
# Builds the final runnable image, starting from the 'base' stage.
FROM python:3.11-slim AS final

# MinerU dependencies, for the cv module
ARG MINERU_DEPS="libglib2.0-0 libgl1"
# LibreOffice is required by MinerU for converting docs to PDF
ARG LIBREOFFICE_DEPS="libreoffice"
# Install Chinese fonts to prevent garbled text when converting docs
ARG LIBREOFFICE_FONT_DEPS="fonts-noto-cjk fonts-wqy-zenhei fonts-wqy-microhei fontconfig"
# Install minimal system dependencies
RUN apt update && \
    apt install --no-install-recommends -y curl \
        ${MINERU_DEPS} ${LIBREOFFICE_DEPS} ${LIBREOFFICE_FONT_DEPS} && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app
# Configure Hugging Face cache path for the application runtime.
# Use the same ARG as in the model-downloader stage to ensure consistency.
ARG HF_MODELS_CACHE_PATH="/app/models_cache"
ENV HF_HOME=${HF_MODELS_CACHE_PATH} \
    HUGGINGFACE_HUB_CACHE=${HF_MODELS_CACHE_PATH} \
    MODELSCOPE_CACHE=${HF_MODELS_CACHE_PATH}

# Copy the pre-downloaded models from the 'model-downloader' stage.
COPY --from=model-downloader ${HF_HOME} ${HF_HOME}
COPY --from=model-downloader /app/magic-pdf.json /app/magic-pdf.json

# Copy the Python virtual environment from the 'install-deps' stage.
COPY --from=install-deps /app/.venv /app/.venv

# Copy the rest of the application code
# COPY . .
# Alternatively, be more specific:
COPY app ./app
COPY scripts ./scripts
COPY run.py ./run.py
COPY serve_config.yaml ./serve_config.yaml

# Expose the port the app runs on (Ray Serve default HTTP port)
EXPOSE 8639
# Expose the Ray dashboard port (optional, but useful)
EXPOSE 8265
# Expose Ray head port (gRPC)
# EXPOSE 6379
# Expose Ray internal gRPC port
# EXPOSE 10001


# Command to run the application
# Option 1: Using the run.py script (simpler for development consistency)
# This will start a local Ray instance within the container.
CMD ["bash", "-c", "source ./.venv/bin/activate && exec python ./run.py"]

# Option 2: Using `ray start` and `serve run` (more explicit for production-like setup)
# This is often preferred for containers as it's more direct.
# CMD ray start --head --disable-usage-stats --dashboard-host 0.0.0.0 --block && \
#     serve run serve_config.yaml --host 0.0.0.0 --port 8639

# Note on CMD:
# The `ray start --head --block` keeps Ray running.
# `serve run` then deploys the application to this Ray instance.
# The `&&` chain ensures `serve run` only executes if `ray start` succeeds.
# `--block` makes `ray start` a foreground process. If it daemonizes, the container might exit.
# If `ray start` daemonizes by default, you might need a wrapper script or `tail -f /dev/null`
# to keep the container alive after `serve run` configures the deployment.
# However, `python run.py` already handles the Ray lifecycle and keeping the process alive.
# So, for this project, `CMD ["python", "run.py"]` is fine and simpler.
