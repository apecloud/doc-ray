ARG IMAGE_TAG=latest
ARG BASE_IMAGE=apecloud/doc-ray:base-${IMAGE_TAG}
ARG MODELS_IMAGE=apecloud/doc-ray:models-${IMAGE_TAG}
ARG FINAL_BASE=base-with-models

FROM ${MODELS_IMAGE} AS models
FROM ${BASE_IMAGE} AS base

FROM base AS base-with-models
COPY --from=models /models_cache /models_cache
COPY --from=models /mineru /mineru
ENV MINERU_CONFIG_JSON=/mineru/mineru.json

FROM ${FINAL_BASE} AS final

WORKDIR /app

# Copy the rest of the application code
# COPY . .
# Alternatively, be more specific:
COPY app ./app
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
# CMD ["python", "./run.py"]

# Option 2: Using `ray start` and `serve run` (more explicit for production-like setup)
# This is often preferred for containers as it's more direct.
CMD ["bash", "-c", \
     "ray start --head --disable-usage-stats --dashboard-host 0.0.0.0 && \
      serve run serve_config.yaml"]
