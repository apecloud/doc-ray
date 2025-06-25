import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

import ray
import ray.exceptions

# Configure logging
logger = logging.getLogger(__name__)


# Default TTL values (can be overridden via actor options)
DEFAULT_JOB_TTL_SECONDS = 30 * 60
DEFAULT_JOB_CLEANUP_INTERVAL_SECONDS = 5 * 60


@ray.remote
class JobStateManager:
    def __init__(
        self,
        ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS,
        cleanup_interval_seconds: int = DEFAULT_JOB_CLEANUP_INTERVAL_SECONDS,
    ):
        self._job_states: Dict[str, Dict[str, Any]] = {}
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        logging.basicConfig(level=logging.INFO)
        logger.info(
            f"JobStateManager Actor initialized with TTL: {self.ttl_seconds}s, Cleanup Interval: {self.cleanup_interval_seconds}s."
        )
        self._cleanup_task = asyncio.create_task(self._ttl_cleanup_loop())

    def submit_job(self, initial_data: Optional[Any] = None) -> str:
        job_id = str(uuid.uuid4())

        # Adapt initial_data_summary for the new structure
        data_summary = "N/A"
        if initial_data and isinstance(initial_data, dict):
            filename = initial_data.get("filename", "N/A")
            content_type = initial_data.get("content_type", "N/A")
            size_bytes = initial_data.get("size", "N/A")
            parser_type_info = initial_data.get("parser_type", "N/A")
            parser_params_info = initial_data.get("parser_params", {})
            data_summary = (
                f"File: {filename}, Type: {content_type}, Size: {size_bytes} bytes, "
                f"Parser: {parser_type_info}, Params: {parser_params_info}"
            )
        elif initial_data:  # Fallback for other types, though not expected now
            data_summary = str(initial_data)[:200]

        self._job_states[job_id] = {
            "status": "processing",
            "result": None,
            "error": None,
            "initial_data_summary": data_summary,
            "last_accessed_time": time.time(),
        }
        logger.info(
            f"Job {job_id} submitted. Initial status: processing. Summary: {data_summary}"
        )
        return job_id

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        if job_id not in self._job_states:
            logger.warning(f"Attempted to get status for unknown job_id: {job_id}")
            return None

        # Touch the job: Update last_accessed_time
        self._job_states[job_id]["last_accessed_time"] = time.time()

        status_info = {
            "job_id": job_id,
            "status": self._job_states[job_id]["status"],
            "error": self._job_states[job_id]["error"],
        }
        logger.info(f"Fetching status for job_id {job_id}: {status_info['status']}")
        return status_info

    async def get_job_result(self, job_id: str) -> Optional[Any]:
        if job_id not in self._job_states:
            logger.warning(f"Attempted to get result for unknown job_id: {job_id}")
            return None

        # Touch the job: Update last_accessed_time
        current_time_for_access = time.time()
        self._job_states[job_id]["last_accessed_time"] = current_time_for_access
        logger.info(
            f"Job {job_id} accessed for result, TTL extended. New access time: {current_time_for_access}"
        )

        job_state = self._job_states[job_id]

        if job_state["status"] == "completed":
            logger.info(f"Fetching result for completed job_id {job_id}.")
            try:
                result_object_ref = job_state["result"]
                if not isinstance(result_object_ref, ray.ObjectRef):
                    logger.error(
                        f"Job {job_id} 'result' field is not an ObjectRef, but type {type(result_object_ref)}. State might be corrupted."
                    )
                    self.update_job_status(
                        job_id,
                        "failed",
                        error_message="Internal error: Result data is corrupted.",
                    )
                    return {
                        "error": "Internal error: Result data is corrupted.",
                        "status": "failed",
                    }

                actual_result_data = await result_object_ref
                return actual_result_data
            except ray.exceptions.ObjectLostError:
                logger.error(
                    f"Result object for job_id {job_id} was lost from Ray object store."
                )
                self.delete_job(job_id)  # Clean up the state for the lost object
                return None  # Treat as not found
            except Exception as e:
                logger.error(
                    f"Error getting result for job {job_id} from object store: {e}",
                    exc_info=True,
                )
                error_msg = f"Failed to retrieve stored result: {str(e)}"
                self.update_job_status(job_id, "failed", error_message=error_msg)
                return {"error": error_msg, "status": "failed"}

        elif job_state["status"] == "failed":
            logger.info(f"Job_id {job_id} failed. Returning error information.")
            return {"error": job_state["error"], "status": "failed"}
        else:
            logger.info(
                f"Job_id {job_id} not yet completed. Status: {job_state['status']}"
            )
            return {
                "status": job_state["status"],
                "message": "Job processing is not yet complete.",
            }

    def update_job_status(
        self, job_id: str, status: str, error_message: Optional[str] = None
    ):
        if job_id not in self._job_states:
            logger.warning(f"Attempted to update status for unknown job_id: {job_id}")
            return False  # Or raise an error

        self._job_states[job_id]["status"] = status
        if error_message:
            self._job_states[job_id]["error"] = error_message
        # Updating status also implies the job is being actively managed
        self._job_states[job_id]["last_accessed_time"] = time.time()
        logger.info(f"Updated status for job_id {job_id} to: {status}.")
        return True

    def store_job_result(self, job_id: str, result_data: Any):
        if job_id not in self._job_states:
            logger.warning(f"Attempted to store result for unknown job_id: {job_id}")
            return False  # Or raise an error

        # Offload result_data; when memory is insufficient, this data will spill to disk.
        result_ref = ray.put(result_data)

        self._job_states[job_id]["result"] = result_ref
        self._job_states[job_id]["status"] = "completed"
        self._job_states[job_id]["last_accessed_time"] = time.time()
        logger.info(
            f"Stored result (ObjectRef: {result_ref.hex()}) for job_id {job_id} and marked as completed."
        )
        return True

    def delete_job(self, job_id: str) -> bool:
        if job_id in self._job_states:
            del self._job_states[job_id]
            logger.info(f"Job {job_id} and its result have been deleted.")
            return True
        else:
            logger.warning(f"Attempted to delete unknown job_id: {job_id}")
            return False

    def get_all_jobs(self) -> Dict[str, Dict[str, Any]]:
        """Utility method to view all job states (for debugging/admin)"""
        # This method is for observation, so we don't update last_accessed_time for all jobs.
        return self._job_states

    async def _ttl_cleanup_loop(self):
        logger.info("TTL cleanup loop started.")
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval_seconds)
                logger.debug("Running TTL cleanup check...")
                await self._cleanup_expired_jobs()
            except asyncio.CancelledError:
                logger.info("TTL cleanup loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in TTL cleanup loop: {e}", exc_info=True)
                # Avoid continuous fast loops on persistent errors, wait before retrying
                await asyncio.sleep(
                    self.cleanup_interval_seconds
                )  # Wait before next attempt

    async def _cleanup_expired_jobs(self):
        now = time.time()
        expired_job_ids = []
        # Iterate over a copy of items if modifying the dict during iteration,
        # or collect IDs first then delete.
        for job_id, job_data in list(self._job_states.items()):
            last_accessed = job_data.get("last_accessed_time")
            # Ensure last_accessed is a valid timestamp before comparison
            if isinstance(last_accessed, (int, float)) and (
                now - last_accessed > self.ttl_seconds
            ):
                expired_job_ids.append(job_id)

        if expired_job_ids:
            logger.info(
                f"TTL: Found {len(expired_job_ids)} expired jobs: {', '.join(expired_job_ids)}"
            )
            for job_id in expired_job_ids:
                logger.info(
                    f"TTL: Deleting expired job {job_id} (last accessed at: {time.ctime(self._job_states[job_id]['last_accessed_time'])})."
                )
                self.delete_job(job_id)  # This is a synchronous method
