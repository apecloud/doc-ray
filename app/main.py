import asyncio  # Required for background task execution
import logging
import os  # Added for os.getenv
from typing import Any, Dict, Optional, Union

import ray
import ray.serve.handle
from fastapi import (  # Removed BackgroundTasks, Added File, UploadFile, Form; File, UploadFile, Form were added
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, field_serializer
from ray import serve

from .mineru_parser import SUPPORTED_EXTENSIONS

# Import project components
from .parser import DocumentParser
from .state_manager import JobStateManager  # Actor

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Async Document Parsing Service",
    description="An API to submit documents for asynchronous parsing, check status, and retrieve results.",
    version="0.1.0",
)


# --- Pydantic Models for Request and Response ---
# SubmitRequest is no longer used directly for the /submit endpoint body with file upload.
# Additional parameters like parser_type and parser_params will be accepted as Form fields.
# class SubmitRequest(BaseModel):
#     pass # Potentially other metadata fields if not using Form for everything
class SubmitResponse(BaseModel):
    job_id: str
    message: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    error: Optional[str] = None


class Result(BaseModel):
    markdown: str
    middle_json: str  # This is likely a JSON string already, or a dict
    images_zip_bytes: Optional[bytes] = None

    @field_serializer("images_zip_bytes")
    def serialize_images_zip_bytes(self, v: Optional[bytes], _info):
        if v is None:
            return None
        import base64

        return base64.b64encode(v).decode("utf-8")


class ResultResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[Result] = None
    error: Optional[str] = None
    message: Optional[str] = None


# --- Standalone Ray Remote Function for Parsing ---
async def _execute_parsing_task_async_logic(  # Renamed to indicate it's the async core
    job_id: str,
    document_object_ref: Union[ray.ObjectRef, bytes],
    filename: str,
    parser_type: Optional[str],
    parser_params: Optional[Dict[str, Any]],
    file_content_type: Optional[str],
    parser_deployment_handle: ray.serve.handle.DeploymentHandle,
    state_manager_actor_handle: JobStateManager,
):
    """
    Core asynchronous logic for parsing.
    """
    logger.info(f"Async parsing logic started for job_id: {job_id}, file: {filename}")
    try:
        result = await parser_deployment_handle.parse.remote(
            document_object_ref,
            filename,
            job_id,
            parser_type,
            parser_params,
        )
        await state_manager_actor_handle.store_job_result.remote(job_id, result)
        logger.info(f"Parsing successful for job_id: {job_id}. Result stored.")
    except Exception as e:
        error_msg = f"Error during remote parsing for job_id {job_id} (file: {filename}): {str(e)}"
        logger.error(error_msg, exc_info=True)
        await state_manager_actor_handle.update_job_status.remote(
            job_id, "failed", error_message=error_msg
        )


@ray.remote
def execute_parsing_task_remotely(
    job_id: str,
    document_object_ref: Union[ray.ObjectRef, bytes],
    filename: str,
    parser_type: Optional[str],
    parser_params: Optional[Dict[str, Any]],
    file_content_type: Optional[str],
    parser_deployment_handle: serve.handle.DeploymentHandle,
    state_manager_actor_handle: JobStateManager,
):
    """
    This synchronous Ray remote function wraps the asynchronous parsing logic.
    It's called by the ServeController.
    """
    logger.info(
        f"Remote parsing task (sync wrapper) started for job_id: {job_id}, file: {filename}"
    )
    # Get or create an event loop to run the async logic
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(
        _execute_parsing_task_async_logic(
            job_id,
            document_object_ref,
            filename,
            parser_type,
            parser_params,
            file_content_type,
            parser_deployment_handle,
            state_manager_actor_handle,
        )
    )


# --- Ray Serve Deployment ---
@serve.deployment(
    # num_replicas=1, # Adjust based on expected load
    # ray_actor_options={"num_cpus": 0.5} # Adjust resource allocation
)
@serve.ingress(app)
class ServeController:
    def __init__(
        self,
        state_manager_actor_handle: JobStateManager,
        parser_deployment_handle: serve.handle.DeploymentHandle,  # Changed to DeploymentHandle
    ):
        self._state_manager: JobStateManager = state_manager_actor_handle
        self._parser_deployment_handle: serve.handle.DeploymentHandle = (
            parser_deployment_handle  # Store the deployment handle
        )
        logger.info(
            "ServeController initialized with StateManager and DocumentParserDeployment handle."
        )

    @app.post("/submit", response_model=SubmitResponse, status_code=202)
    async def submit_document(
        self,
        file: UploadFile = File(...),
    ):
        """
        Submits a document (via file upload) for asynchronous parsing.
        Optionally specify a parser_type (string) and parser_params (JSON string).
        """
        logger.info(f"Received submission request with file: '{file.filename}', ")

        if not file.filename or file.size == 0:
            raise HTTPException(
                status_code=400, detail="File seems to be empty or invalid."
            )

        # Check if the file format is supported
        file_extension = os.path.splitext(file.filename)[1].lower()
        if file_extension not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file format: {file_extension}. Supported formats are: {', '.join(SUPPORTED_EXTENSIONS)}",
            )

        document_content_bytes = await file.read()
        await file.close()  # Important to close the file after reading

        # Store the document content in Ray object store
        document_object_ref = ray.put(document_content_bytes)
        logger.info(
            f"Document content for '{file.filename}' stored in Ray object store: {document_object_ref.hex()}"
        )

        job_initial_data = {
            "filename": file.filename,
            "content_type": file.content_type,
            "size": file.size,
        }
        job_id = await self._state_manager.submit_job.remote(
            initial_data=job_initial_data
        )
        logger.info(f"Job {job_id} created by StateManager for file '{file.filename}'.")

        # Directly invoke the parsing task as a remote method on this actor instance.
        # This call is non-blocking and returns an ObjectRef immediately (which we ignore here).
        # The task will be scheduled and executed by Ray on this actor.

        # Launch the standalone Ray remote function for parsing
        execute_parsing_task_remotely.remote(
            job_id,
            document_object_ref,
            file.filename,
            None,
            None,
            file.content_type,
            self._parser_deployment_handle,  # Pass the parser handle
            self._state_manager,  # Pass the state manager handle
        )
        logger.info(
            f"Parsing task for job_id {job_id} (file: '{file.filename}') submitted to run on Ray actor."
        )
        return SubmitResponse(job_id=job_id, message="Document submitted for parsing.")

    @app.get("/status/{job_id}", response_model=StatusResponse)
    async def get_status(self, job_id: str):
        """
        Checks the parsing status of a document.
        """
        logger.info(f"Received status request for job_id: {job_id}")
        status_info = await self._state_manager.get_job_status.remote(job_id)
        if not status_info:
            raise HTTPException(status_code=404, detail=f"Job ID '{job_id}' not found.")
        return StatusResponse(**status_info)

    @app.get("/result/{job_id}", response_model=ResultResponse)
    async def get_result(self, job_id: str):
        """
        Retrieves the parsing result of a document.
        """
        logger.info(f"Received result request for job_id: {job_id}")
        status_info = await self._state_manager.get_job_status.remote(
            job_id
        )  # Check status first
        if not status_info:
            raise HTTPException(status_code=404, detail=f"Job ID '{job_id}' not found.")

        current_status = status_info["status"]

        if current_status == "completed":
            result_data = await self._state_manager.get_job_result.remote(job_id)
            return ResultResponse(
                job_id=job_id,
                status=current_status,
                result=Result(
                    markdown=result_data.markdown,
                    middle_json=result_data.middle_json,
                    images_zip_bytes=result_data.images_zip_bytes,
                ),
            )
        elif current_status == "failed":
            error_detail = await self._state_manager.get_job_result.remote(
                job_id
            )  # This will contain the error
            return ResultResponse(
                job_id=job_id,
                status=current_status,
                error=error_detail.get("error") if error_detail else "Unknown error",
            )
        elif current_status == "processing":
            return ResultResponse(
                job_id=job_id,
                status=current_status,
                message="Parsing is still in progress.",
            )
        else:  # Other statuses or unexpected
            return ResultResponse(
                job_id=job_id,
                status=current_status,
                message=f"Job is in state: {current_status}.",
            )


# --- Application Entrypoint for Ray Serve ---
# This defines how Ray Serve should build and run the application.
# It's common to define the actor handles and other resources here.

# 1. Initialize Ray (if not already initialized, e.g. by `ray start`)
# Ensure Ray is initialized. In a script, you might do:
# if not ray.is_initialized():
#   ray.init(address='auto' if you are connecting to an existing cluster, or leave empty for local)

# 2. Create or get the named JobStateManager actor
# Using a named actor allows it to persist across deployments (if configured) and be accessible.
try:
    # This should be done outside the deployment class if the actor should persist across controller replicas/updates
    # or if it's a singleton for the application.
    # In a real scenario, you'd likely start this actor when the Ray cluster/job starts.
    # For `serve run config.yaml`, Ray handles actor creation if defined in the config or if obtained via options.
    # Here, we assume it will be created/obtained when the ServeController is instantiated.
    # To make it truly robust for `serve run` and different deployment scenarios,
    # actor creation/retrieval needs careful consideration of the Ray application lifecycle.
    # For simplicity in this example, we'll pass it during binding.

    # A common pattern is to get or create the actor handle before defining the bound application.
    # This handle can then be passed to the ServeController.
    state_manager_actor = JobStateManager.options(
        name="JobStateManagerActor", get_if_exists=True, lifetime="detached"
    ).remote()
    logger.info("JobStateManagerActor handle obtained/created.")
except Exception as e:
    logger.error(
        f"Failed to get or create JobStateManagerActor: {e}. This is critical."
    )
    # Depending on the setup, you might want to raise an exception here to stop deployment.
    # For now, we log and proceed, but the service will likely fail.
    state_manager_actor = None  # This will cause issues later if not handled

# 3. DocumentParser is now a Serve Deployment. It will be bound into the application.
#    The handle will be created by `DocumentParser.bind()` below.

# 4. Bind the ServeController with its dependencies to the application.
# This is what `serve run` will use based on the import path in the config file.
# (e.g., `doc_parser_service.app.main:app_builder` if we named this `app_builder`)
# Or, if the config points to `doc_parser_service.app.main:controller_app`, it will be this.
# For `serve.run(ServeController.bind(...))` in a Python script, this is how you'd do it.
if state_manager_actor:  # Ensure StateManager is available
    # DocumentParser is imported from .parser
    # Its .bind() method creates a DeploymentHandle that ServeController will use.
    # Ray Serve will manage the lifecycle of DocumentParserDeployment.
    entrypoint = ServeController.bind(
        state_manager_actor_handle=state_manager_actor,
        parser_deployment_handle=DocumentParser.bind(),  # Bind DocumentParser deployment
    )
    logger.info("ServeController bound with dependencies. Ready for serving.")
else:
    logger.critical(
        "JobStateManagerActor handle is not available. Cannot bind ServeController."
    )

    # Fallback or error handling:
    # You might define a simple FastAPI app that returns an error if the critical actor is missing.
    # For now, this means `serve run` might fail if it tries to import `entrypoint` and it's not defined.
    # To make it runnable even in this error state (for debugging config issues):
    @app.get("/health")
    def health_check_error():
        return {
            "status": "error",
            "message": "JobStateManagerActor could not be initialized.",
        }

    entrypoint = (
        app  # Serve the basic FastAPI app with an error message on health check
    )
    logger.info(
        "Serving a fallback FastAPI app due to JobStateManagerActor initialization failure."
    )

# For local testing with `python doc_parser_service/app/main.py` (if you add `if __name__ == "__main__": serve.run(entrypoint)`)
# or when Ray Serve CLI picks up this file.
# The `serve run config.yaml` will typically look for an import path like `module:application_object`.
# So if your config.yaml has `import_path: doc_parser_service.app.main:entrypoint`,
# Ray Serve will import this file and use the `entrypoint` object created above.
