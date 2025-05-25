import ray
import uuid
import logging
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@ray.remote
class JobStateManager:
    def __init__(self):
        self._job_states: Dict[str, Dict[str, Any]] = {}
        logger.info("JobStateManager Actor initialized.")

    def submit_job(self, initial_data: Optional[Any] = None) -> str:
        job_id = str(uuid.uuid4())
        self._job_states[job_id] = {
            "status": "processing",
            "result": None,
            "error": None,
            "initial_data_summary": str(initial_data)[:200] if initial_data else "N/A" # Store a summary
        }
        logger.info(f"Job {job_id} submitted. Initial status: processing.")
        return job_id

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        if job_id not in self._job_states:
            logger.warning(f"Attempted to get status for unknown job_id: {job_id}")
            return None
        
        status_info = {
            "job_id": job_id,
            "status": self._job_states[job_id]["status"],
            "error": self._job_states[job_id]["error"]
        }
        logger.info(f"Fetching status for job_id {job_id}: {status_info['status']}")
        return status_info

    def get_job_result(self, job_id: str) -> Optional[Any]:
        if job_id not in self._job_states:
            logger.warning(f"Attempted to get result for unknown job_id: {job_id}")
            return None
        
        if self._job_states[job_id]["status"] == "completed":
            logger.info(f"Fetching result for completed job_id {job_id}.")
            return self._job_states[job_id]["result"]
        elif self._job_states[job_id]["status"] == "failed":
            logger.info(f"Job_id {job_id} failed. Returning error information.")
            return {"error": self._job_states[job_id]["error"], "status": "failed"}
        else:
            logger.info(f"Job_id {job_id} not yet completed. Status: {self._job_states[job_id]['status']}")
            return {"status": self._job_states[job_id]["status"], "message": "Job processing is not yet complete."}

    def update_job_status(self, job_id: str, status: str, error_message: Optional[str] = None):
        if job_id not in self._job_states:
            logger.warning(f"Attempted to update status for unknown job_id: {job_id}")
            return False # Or raise an error
        
        self._job_states[job_id]["status"] = status
        if error_message:
            self._job_states[job_id]["error"] = error_message
        logger.info(f"Updated status for job_id {job_id} to: {status}.")
        return True

    def store_job_result(self, job_id: str, result_data: Any):
        if job_id not in self._job_states:
            logger.warning(f"Attempted to store result for unknown job_id: {job_id}")
            return False # Or raise an error
            
        self._job_states[job_id]["result"] = result_data
        self._job_states[job_id]["status"] = "completed" # Automatically mark as completed when result is stored
        logger.info(f"Stored result for job_id {job_id} and marked as completed.")
        return True

    def get_all_jobs(self) -> Dict[str, Dict[str, Any]]:
        ''' Utility method to view all job states (for debugging/admin) '''
        return self._job_states

# Example of how to use the actor (for testing purposes)
if __name__ == "__main__":
    ray.init(ignore_reinit_error=True)
    
    async def main():
        # Create or get the actor
        # Actors are named, so getting it again with the same name returns the existing actor.
        try:
            state_manager = JobStateManager.options(name="JobStateManagerActor", get_if_exists=True).remote()
            print("JobStateManager actor created or retrieved.")
        except Exception as e:
            print(f"Error creating/getting state manager actor: {e}")
            return

        # Test submit_job
        job_id_1 = await state_manager.submit_job.remote(initial_data="Test document content 1")
        print(f"Submitted job 1 with ID: {job_id_1}")

        job_id_2 = await state_manager.submit_job.remote(initial_data="Another test file")
        print(f"Submitted job 2 with ID: {job_id_2}")

        # Test get_job_status
        status_1 = await state_manager.get_job_status.remote(job_id_1)
        print(f"Status for job {job_id_1}: {status_1}")

        # Test update_job_status
        await state_manager.update_job_status.remote(job_id_1, "processing_step_2")
        status_1_updated = await state_manager.get_job_status.remote(job_id_1)
        print(f"Updated status for job {job_id_1}: {status_1_updated}")
        
        # Test store_job_result
        await state_manager.store_job_result.remote(job_id_1, {"parsed_text": "This is the parsed result for job 1."})
        result_1 = await state_manager.get_job_result.remote(job_id_1)
        print(f"Result for job {job_id_1}: {result_1}")
        status_1_final = await state_manager.get_job_status.remote(job_id_1)
        print(f"Final status for job {job_id_1}: {status_1_final}")

        # Test getting result for a job still processing
        result_2_pending = await state_manager.get_job_result.remote(job_id_2)
        print(f"Result for pending job {job_id_2}: {result_2_pending}")

        # Test getting status for a non-existent job
        status_unknown = await state_manager.get_job_status.remote("unknown_job_id")
        print(f"Status for unknown job: {status_unknown}")
        
        all_jobs = await state_manager.get_all_jobs.remote()
        print(f"All jobs: {all_jobs}")

    import asyncio
    asyncio.run(main())
    
    # Clean up actor after test (optional, as it's a named actor)
    # try:
    #    actor_to_kill = ray.get_actor("JobStateManagerActor")
    #    ray.kill(actor_to_kill)
    #    print("JobStateManager actor killed.")
    # except Exception as e:
    #    print(f"Could not kill actor: {e}")
    ray.shutdown()
