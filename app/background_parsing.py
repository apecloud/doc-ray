import time
from typing import Any, Dict, Optional

import ray
from ray.actor import ActorHandle
from ray.serve.handle import DeploymentHandle


@ray.remote(name="BackgroundParsingActor", max_concurrency=16)
class BackgroundParsingActor:
    """
    This actor handles the document parsing in the background.
    It's a singleton actor with limited concurrency.
    """

    # async def parse_document(
    #     self,
    #     job_id: str,
    #     document_content_bytes: bytes,
    #     filename: str,
    #     parser_type: Optional[str],
    #     parser_params: Optional[Dict[str, Any]],
    #     file_content_type: Optional[str],
    #     parser_deployment_handle: ray.serve.handle.DeploymentHandle,
    #     state_manager_actor_handle: JobStateManager,
    # ):
    #     # Logger setup inside the actor method if not configured globally for actors
    #     # For simplicity, assuming ray.logger is available and configured
    #     actor_logger = ray.logger  # or logging.getLogger(__name__) if preferred

    #     try:
    #         actor_logger.info(
    #             f"BackgroundParsingActor: Start parsing for job_id: {job_id}, file: {filename}"
    #         )
    #         start_time = time.time()
    #         loop = asyncio.get_running_loop()
    #         # MinerUParser modifies some global state within the magic-pdf library,
    #         # so it needs to be placed in a named actor to isolate it from other processes.
    #         mineru_parser = MinerUParser(parser_deployment_handle) # Renamed for clarity
    #         # Run the potentially blocking parse method in a thread pool executor
    #         parse_result_obj = await loop.run_in_executor(
    #             None,  # Use default ThreadPoolExecutor
    #             mineru_parser.parse,
    #             document_content_bytes,
    #             filename,
    #         )
    #         end_time = time.time()
    #         parsing_duration = end_time - start_time
    #         actor_logger.info(
    #             f"BackgroundParsingActor: Parsing for job_id: {job_id} (file: {filename}) took {parsing_duration:.2f} seconds."
    #         )
    #         await state_manager_actor_handle.store_job_result.remote(job_id, parse_result_obj)
    #         actor_logger.info(
    #             f"BackgroundParsingActor: Parsing successful for job_id: {job_id}."
    #         )
    #     except Exception as e:
    #         error_msg = f"BackgroundParsingActor: Error during parsing for job_id {job_id} (file: {filename}): {str(e)}"
    #         actor_logger.error(error_msg, exc_info=True)
    #         await state_manager_actor_handle.update_job_status.remote(
    #             job_id, "failed", error_message=error_msg
    #         )

    async def parse_document(
        self,
        job_id: str,
        document_content_bytes: bytes,
        filename: str,
        parser_type: Optional[str],
        parser_params: Optional[Dict[str, Any]],
        file_content_type: Optional[str],
        parser_deployment_handle: DeploymentHandle,
        state_manager_actor_handle: ActorHandle,
    ):
        # Logger setup inside the actor method if not configured globally for actors
        # For simplicity, assuming ray.logger is available and configured
        actor_logger = ray.logger  # or logging.getLogger(__name__) if preferred

        try:
            actor_logger.info(
                f"BackgroundParsingActor: Start parsing for job_id: {job_id}, file: {filename}"
            )
            start_time = time.time()
            result = await parser_deployment_handle.parse.remote(
                document_content_bytes, filename
            )
            end_time = time.time()
            parsing_duration = end_time - start_time
            actor_logger.info(
                f"BackgroundParsingActor: Parsing for job_id: {job_id} (file: {filename}) took {parsing_duration:.2f} seconds."
            )
            await state_manager_actor_handle.store_job_result.remote(job_id, result)
            actor_logger.info(
                f"BackgroundParsingActor: Parsing successful for job_id: {job_id}."
            )
        except Exception as e:
            error_msg = f"BackgroundParsingActor: Error during parsing for job_id {job_id} (file: {filename}): {str(e)}"
            actor_logger.error(error_msg, exc_info=True)
            await state_manager_actor_handle.update_job_status.remote(
                job_id, "failed", error_message=error_msg
            )