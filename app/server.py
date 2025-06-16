import logging
import os
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from ray import serve
from ray.actor import ActorHandle
from ray.serve.handle import DeploymentHandle

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

# TODO: support image formats
SUPPORTED_EXTENSIONS = [
    ".pdf",
    # convert to .pdf first
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
]


# --- Pydantic Models for Request and Response ---
class SubmitResponse(BaseModel):
    job_id: str
    message: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    error: Optional[str] = None


class Result(BaseModel):
    markdown: str
    middle_json: str
    images: Dict[str, str]


class ResultResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[Result] = None
    error: Optional[str] = None
    message: Optional[str] = None


class DeleteResponse(BaseModel):
    job_id: str
    message: str


# --- Ray Serve Deployment ---
@serve.deployment(
    # num_replicas=1, # Adjust based on expected load
    # ray_actor_options={"num_cpus": 0.5} # Adjust resource allocation
)
@serve.ingress(app)
class ServeController:
    def __init__(
        self,
        state_manager_actor_handle: ActorHandle,
        background_parser_actor_handle: ActorHandle,
        parser_deployment_handle: DeploymentHandle,
    ):
        self._state_manager: ActorHandle = state_manager_actor_handle
        self._background_parser: ActorHandle = background_parser_actor_handle
        self._parser_deployment_handle: DeploymentHandle = parser_deployment_handle

        logger.info(
            "ServeController initialized with StateManager, BackgroundParsingActor, "
            "and DocumentParserDeployment handle."
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

        # Call the method on the BackgroundParsingActor
        self._background_parser.parse_document.remote(
            job_id,
            document_content_bytes,
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
                    markdown=result_data.get("markdown", ""),
                    middle_json=result_data.get("middle_json", ""),
                    images=result_data.get("images", []),
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

    @app.delete("/result/{job_id}", response_model=DeleteResponse)
    async def delete_result(self, job_id: str):
        """
        Deletes a job and its associated result to free up memory.
        """
        logger.info(f"Received delete request for job_id: {job_id}")
        await self._state_manager.delete_job.remote(job_id)
        return DeleteResponse(
            job_id=job_id, message="Job and result deleted successfully."
        )
