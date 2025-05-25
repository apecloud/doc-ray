import ray
from ray import serve
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import logging
import asyncio # Required for background task execution
from typing import Any, Optional # Added as per instruction

# Import project components
from .parser import DocumentParser
from .state_manager import JobStateManager # Actor

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Async Document Parsing Service",
    description="An API to submit documents for asynchronous parsing, check status, and retrieve results.",
    version="0.1.0"
)

# --- Pydantic Models for Request and Response ---
class SubmitRequest(BaseModel):
    document_data: str # In a real app, this might be more complex, e.g., file upload or URL

class SubmitResponse(BaseModel):
    job_id: str
    message: str

class StatusResponse(BaseModel):
    job_id: str
    status: str
    error: Optional[str] = None

class ResultResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None


# --- Ray Serve Deployment ---
@serve.deployment(
    # num_replicas=1, # Adjust based on expected load
    # ray_actor_options={"num_cpus": 0.5} # Adjust resource allocation
)
@serve.ingress(app)
class ServeController:
    def __init__(self, state_manager_actor_handle, parser_instance):
        self._state_manager: JobStateManager = state_manager_actor_handle
        self._parser: DocumentParser = parser_instance
        logger.info("ServeController initialized with StateManager handle and Parser instance.")

    async def _run_parsing_task(self, job_id: str, document_data: str):
        logger.info(f"Background task started for job_id: {job_id}")
        try:
            # Ensure parser's parse method is awaitable if it's async
            parsed_result = await self._parser.parse(document_data, job_id)
            await self._state_manager.store_job_result.remote(job_id, parsed_result)
            logger.info(f"Parsing successful for job_id: {job_id}. Result stored.")
        except Exception as e:
            error_msg = f"Error during parsing for job_id {job_id}: {str(e)}"
            logger.error(error_msg)
            await self._state_manager.update_job_status.remote(job_id, "failed", error_message=error_msg)

    @app.post("/submit", response_model=SubmitResponse, status_code=202)
    async def submit_document(self, request: SubmitRequest, background_tasks: BackgroundTasks):
        '''
        Submits a document for asynchronous parsing.
        '''
        logger.info(f"Received submission request with data: '{request.document_data[:30]}...'")
        if not request.document_data:
            raise HTTPException(status_code=400, detail="Document data cannot be empty.")

        job_id = await self._state_manager.submit_job.remote(initial_data=request.document_data)
        logger.info(f"Job {job_id} created by StateManager.")

        # Add the parsing task to run in the background
        # Using FastAPI's BackgroundTasks for this, which will run after the response is sent.
        # Alternatively, for longer tasks not tied to FastAPI's event loop,
        # one could use ray.remote functions or actor methods directly if they are detached.
        background_tasks.add_task(self._run_parsing_task, job_id, request.document_data)
        
        logger.info(f"Parsing task for job_id {job_id} added to background.")
        return SubmitResponse(job_id=job_id, message="Document submitted for parsing.")

    @app.get("/status/{job_id}", response_model=StatusResponse)
    async def get_status(self, job_id: str):
        '''
        Checks the parsing status of a document.
        '''
        logger.info(f"Received status request for job_id: {job_id}")
        status_info = await self._state_manager.get_job_status.remote(job_id)
        if not status_info:
            raise HTTPException(status_code=404, detail=f"Job ID '{job_id}' not found.")
        return StatusResponse(**status_info)

    @app.get("/result/{job_id}", response_model=ResultResponse)
    async def get_result(self, job_id: str):
        '''
        Retrieves the parsing result of a document.
        '''
        logger.info(f"Received result request for job_id: {job_id}")
        status_info = await self._state_manager.get_job_status.remote(job_id) # Check status first
        if not status_info:
            raise HTTPException(status_code=404, detail=f"Job ID '{job_id}' not found.")

        current_status = status_info["status"]
        
        if current_status == "completed":
            result_data = await self._state_manager.get_job_result.remote(job_id)
            return ResultResponse(job_id=job_id, status=current_status, result=result_data)
        elif current_status == "failed":
            error_detail = await self._state_manager.get_job_result.remote(job_id) # This will contain the error
            return ResultResponse(job_id=job_id, status=current_status, error=error_detail.get("error") if error_detail else "Unknown error")
        elif current_status == "processing":
            return ResultResponse(job_id=job_id, status=current_status, message="Parsing is still in progress.")
        else: # Other statuses or unexpected
            return ResultResponse(job_id=job_id, status=current_status, message=f"Job is in state: {current_status}.")


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
    state_manager_actor = JobStateManager.options(name="JobStateManagerActor", get_if_exists=True, lifetime="detached").remote()
    logger.info("JobStateManagerActor handle obtained/created.")
except Exception as e:
    logger.error(f"Failed to get or create JobStateManagerActor: {e}. This is critical.")
    # Depending on the setup, you might want to raise an exception here to stop deployment.
    # For now, we log and proceed, but the service will likely fail.
    state_manager_actor = None # This will cause issues later if not handled

# 3. Create an instance of the DocumentParser
# This can be a simple instance if it's stateless, or you might have a pool of parser actors.
document_parser_instance = DocumentParser() # Assuming DocumentParser is stateless or manages its own state appropriately.
logger.info("DocumentParser instance created.")

# 4. Bind the ServeController with its dependencies to the application.
# This is what `serve run` will use based on the import path in the config file.
# (e.g., `doc_parser_service.app.main:app_builder` if we named this `app_builder`)
# Or, if the config points to `doc_parser_service.app.main:controller_app`, it will be this.
# For `serve.run(ServeController.bind(...))` in a Python script, this is how you'd do it.
if state_manager_actor:
    # The application to be served. `serve run` will pick this up if the config points to it.
    # The name 'app_to_serve' is arbitrary but should match what you might specify in a config file
    # if you are using `serve build` and `serve deploy` with a YAML config.
    # If you use `serve run my_script.py:my_app_definition`, then `my_app_definition` would be this.
    # Let's call it `entrypoint` to be clear.
    entrypoint = ServeController.bind(state_manager_actor_handle=state_manager_actor, parser_instance=document_parser_instance)
    logger.info("ServeController bound with dependencies. Ready for serving.")
else:
    logger.critical("JobStateManagerActor handle is not available. Cannot bind ServeController.")
    # Fallback or error handling:
    # You might define a simple FastAPI app that returns an error if the critical actor is missing.
    # For now, this means `serve run` might fail if it tries to import `entrypoint` and it's not defined.
    # To make it runnable even in this error state (for debugging config issues):
    @app.get("/health")
    def health_check_error():
        return {"status": "error", "message": "JobStateManagerActor could not be initialized."}
    entrypoint = app # Serve the basic FastAPI app with an error message on health check
    logger.info("Serving a fallback FastAPI app due to JobStateManagerActor initialization failure.")

# For local testing with `python doc_parser_service/app/main.py` (if you add `if __name__ == "__main__": serve.run(entrypoint)`)
# or when Ray Serve CLI picks up this file.
# The `serve run config.yaml` will typically look for an import path like `module:application_object`.
# So if your config.yaml has `import_path: doc_parser_service.app.main:entrypoint`,
# Ray Serve will import this file and use the `entrypoint` object created above.
