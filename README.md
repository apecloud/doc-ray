doc-ray
=======

This project implements an asynchronous document parsing service using Ray Serve. Users can submit documents, poll for parsing status, and retrieve results. It's designed for both standalone local development and cluster deployment.

## Features

- **Asynchronous API**: Submit, status, and result endpoints.
- **Scalable**: Built on Ray Serve, allowing for scaling from a single machine to a cluster.
- **State Management**: Uses a Ray Actor (`JobStateManager`) to manage job states, ensuring consistency even in a clustered environment.
- **Containerized**: Dockerfile provided for easy building and deployment.
- **Modern Tooling**: Uses `uv` for package management.

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Docker (for containerized deployment)
- Ray (implicitly managed by `uv` and Docker setup for the most part)

## Development Setup

1.  **Clone the repository**:
    ```bash
    # git clone https://github.com/apecloud/doc-ray
    # cd doc-ray
    ```

2.  **Create and activate a virtual environment using `uv`**:
    ```bash
    uv venv
    source .venv/bin/activate
    ```

3.  **Install dependencies using `uv`**:
    ```bash
    uv sync --prerelease=allow --all-extras
    ```

4.  **Prepare MinerU prerequisites**:
    Run the script to download models required by MinerU and generate the `magic-pdf.json` file.
    ```bash
    make download-models
    ```

5.  **Run the service locally**:
    The `run.py` script initializes a local Ray instance and deploys the Ray Serve application.
    ```bash
    python run.py
    ```
    The service will typically be available at `http://localhost:8639`.
    - API documentation (Swagger UI) is often available at `http://localhost:8639/docs`.
    - Ray Dashboard: `http://localhost:8265`.

## API Endpoints

-   **POST `/submit`**: Submits a document for parsing.
    -   Request Body: `{"document_data": "content of the document"}`
    -   Response: `{"job_id": "unique_job_id", "message": "Document submitted..."}` (Status 202)
-   **GET `/status/{job_id}`**: Checks the parsing status.
    -   Response: `{"job_id": "unique_job_id", "status": "processing|completed|failed", "error": "error message if failed"}`
-   **GET `/result/{job_id}`**: Retrieves the parsing result.
    -   Response (if completed): `{"job_id": "unique_job_id", "status": "completed", "result": {"markdown": "parsed markdown content"}}`
    -   Response (if pending/failed): `{"job_id": "unique_job_id", "status": "processing|failed", "message": "...", "error": "..."}`
-   **DELETE `/result/{job_id}`**: Deletes a job and its result to free up resources.
    -   Response (if successful): `{"job_id": "unique_job_id", "message": "Job and result deleted successfully."}` (Status 200)

## Deployment

### Standalone Mode with Docker

1.  **(Optional) Build the Docker image locally**:

    ```bash
    make build-standalone
    ```

2.  **Run the Docker container**:
    ```bash
    docker run -d -p 8639:8639 -p 8265:8265 --gpus=all --name doc-ray apecloud/doc-ray:standalone-latest
    ```
    - `-d`: Run in detached mode.
    - `-p 8639:8639`: Maps the container's port 8639 (Ray Serve HTTP) to the host's port 8639.
    - `-p 8265:8265`: Maps the container's port 8265 (Ray Dashboard) to the host's port 8265.
    - `--gpus=all`: Enables GPU support if available. Omit this flag if not supported (e.g., on Docker Desktop for macOS). **Deployment with GPUs is strongly recommended.**
    - `--name doc-ray`: Assigns a name to the container for easier management.

    The service will be accessible at `http://localhost:8639` on your host machine.

### Cluster Mode (To be verified)

1.  **Set up a Ray Cluster**:
    Follow the official Ray documentation to set up a multi-node Ray cluster.
    (See: [Ray Cluster Setup](https://docs.ray.io/en/latest/cluster/getting-started.html))

2.  **Deploy the application to the cluster**:
    Once your Ray cluster is running and your local environment is configured to connect to it (e.g., via `ray.init(address="ray://<head_node_ip>:10001")` or by setting `RAY_ADDRESS`), you can deploy the application using the Ray Serve CLI with the configuration file.

    Ensure your application code and `serve_config.yaml` are accessible to the machine from where you run the deploy command, or are part of your runtime environment.

    ```bash
    # Example: If connecting to a running Ray cluster
    # Ensure your context points to the cluster head.
    # Then, from the doc_parser_service directory:
    serve run serve_config.yaml
    ```
    This command submits the application defined in `serve_config.yaml` to the connected Ray cluster. The `JobStateManager` actor will ensure that state is shared across the cluster, and Ray Serve will handle routing requests to appropriate replicas.

    For robust cluster deployment, consider:
    - Packaging your application code and dependencies into a runtime environment (`working_dir` or `py_modules` with a requirements file) specified in your Serve config or when connecting to Ray. The Docker image itself can also be used as a basis for nodes in a Kubernetes-based Ray cluster (e.g., using KubeRay).
    - Configuring `num_replicas`, CPU/GPU resources, and other deployment options in `serve_config.yaml` or directly in the `app.main.py` deployment definition for production needs.
