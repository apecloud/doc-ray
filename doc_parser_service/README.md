# Asynchronous Document Parsing Service with Ray Serve

This project implements an asynchronous document parsing service using Ray Serve. Users can submit documents, poll for parsing status, and retrieve results. It's designed for both standalone local development and cluster deployment.

## Features

- **Asynchronous API**: Submit, status, and result endpoints.
- **Scalable**: Built on Ray Serve, allowing for scaling from a single machine to a cluster.
- **State Management**: Uses a Ray Actor (`JobStateManager`) to manage job states, ensuring consistency even in a clustered environment.
- **Containerized**: Dockerfile provided for easy building and deployment.
- **Modern Tooling**: Uses `uv` for package management.

## Project Structure

```
doc_parser_service/
├── app/                    # Main application code
│   ├── __init__.py
│   ├── main.py             # Ray Serve application, FastAPI endpoints
│   ├── parser.py           # Document parsing logic (simulated)
│   └── state_manager.py    # Ray Actor for managing job states
├── scripts/                # Utility scripts (if any in future)
├── tests/                  # Unit and integration tests (if any in future)
├── Dockerfile              # For building the Docker image
├── pyproject.toml          # Project metadata and dependencies for uv
├── README.md               # This file
├── run.py                  # Script to run the service locally
└── serve_config.yaml       # Ray Serve application configuration
```

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Docker (for containerized deployment)
- Ray (implicitly managed by `uv` and Docker setup for the most part)

## Development Setup

1.  **Clone the repository (if applicable)**:
    ```bash
    # git clone <repository-url>
    # cd doc_parser_service
    ```

2.  **Create and activate a virtual environment using `uv`**:
    ```bash
    uv venv
    source .venv/bin/activate  # On Linux/macOS
    # .venv\Scriptsctivate    # On Windows
    ```

3.  **Install dependencies using `uv`**:
    ```bash
    uv pip install -r pyproject.toml
    ```
    *(Note: `pyproject.toml` is used directly as a requirements file here. If you generate a `requirements.txt` or `uv.lock` from it, you'd use that instead.)*

4.  **Run the service locally**:
    The `run.py` script initializes a local Ray instance and deploys the Ray Serve application.
    ```bash
    python run.py
    ```
    The service will typically be available at `http://localhost:8000`.
    - API documentation (Swagger UI) is often available at `http://localhost:8000/docs`.
    - Ray Dashboard: `http://localhost:8265`.

## API Endpoints

-   **POST `/submit`**: Submits a document for parsing.
    -   Request Body: `{"document_data": "content of the document"}`
    -   Response: `{"job_id": "unique_job_id", "message": "Document submitted..."}` (Status 202)
-   **GET `/status/{job_id}`**: Checks the parsing status.
    -   Response: `{"job_id": "unique_job_id", "status": "processing|completed|failed", "error": "error message if failed"}`
-   **GET `/result/{job_id}`**: Retrieves the parsing result.
    -   Response (if completed): `{"job_id": "unique_job_id", "status": "completed", "result": "parsed_content"}`
    -   Response (if pending/failed): `{"job_id": "unique_job_id", "status": "processing|failed", "message": "...", "error": "..."}`

## Deployment

### Standalone Mode with Docker

The provided `Dockerfile` packages the application into a container.

1.  **Build the Docker image**:
    From the `doc_parser_service` directory:
    ```bash
    docker build -t doc-parser-service .
    ```

2.  **Run the Docker container**:
    ```bash
    docker run -d -p 8000:8000 -p 8265:8265 --name doc-parser-app doc-parser-service
    ```
    - `-d`: Run in detached mode.
    - `-p 8000:8000`: Maps the container's port 8000 (Ray Serve HTTP) to the host's port 8000.
    - `-p 8265:8265`: Maps the container's port 8265 (Ray Dashboard) to the host's port 8265.
    - `--name doc-parser-app`: Assigns a name to the container for easier management.

    The service will be accessible at `http://localhost:8000` on your host machine.

### Cluster Mode

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

## Future Enhancements
- Add actual document parsing libraries (e.g., Tika, PyMuPDF).
- Implement more robust error handling and retries.
- Add unit and integration tests.
- Secure API endpoints.
- Persist job states to an external database instead of just in-memory in the actor (for resilience beyond actor lifetime if the actor crashes or the cluster restarts without detached actor recovery).
```
