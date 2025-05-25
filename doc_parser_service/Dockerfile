# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONUNBUFFERED 1
ENV UV_HOME "/opt/uv"
ENV PATH="/opt/uv:$PATH"

# Install uv globally
RUN apt-get update && apt-get install -y curl build-essential && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the pyproject.toml and uv.lock (if it exists) first
# This allows Docker to leverage caching for dependencies if they haven't changed
COPY pyproject.toml ./
# COPY uv.lock ./ # Uncomment if you generate and use a lock file

# Install dependencies using uv
# --system installs to the system Python, which is fine for containers
# --no-cache to reduce image size
# Add --frozen-lockfile if uv.lock is used
# pyproject.toml should list ray[serve] and fastapi as dependencies.
# This command installs dependencies specified in pyproject.toml.
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy the rest of the application code
COPY . .
# Alternatively, be more specific:
# COPY app ./app
# COPY run.py ./run.py
# COPY serve_config.yaml ./serve_config.yaml

# Expose the port the app runs on (Ray Serve default HTTP port)
EXPOSE 8000
# Expose the Ray dashboard port (optional, but useful)
EXPOSE 8265
# Expose Ray head port (gRPC)
EXPOSE 6379
# Expose Ray internal gRPC port
EXPOSE 10001


# Command to run the application
# Option 1: Using the run.py script (simpler for development consistency)
# This will start a local Ray instance within the container.
CMD ["python", "run.py"]

# Option 2: Using `ray start` and `serve run` (more explicit for production-like setup)
# This is often preferred for containers as it's more direct.
# CMD ray start --head --disable-usage-stats --dashboard-host 0.0.0.0 --block && \
#     serve run serve_config.yaml --host 0.0.0.0 --port 8000

# Note on CMD:
# The `ray start --head --block` keeps Ray running.
# `serve run` then deploys the application to this Ray instance.
# The `&&` chain ensures `serve run` only executes if `ray start` succeeds.
# `--block` makes `ray start` a foreground process. If it daemonizes, the container might exit.
# If `ray start` daemonizes by default, you might need a wrapper script or `tail -f /dev/null`
# to keep the container alive after `serve run` configures the deployment.
# However, `python run.py` already handles the Ray lifecycle and keeping the process alive.
# So, for this project, `CMD ["python", "run.py"]` is fine and simpler.
