import asyncio
import os
import time
from dataclasses import asdict
from typing import Any, Dict, Optional

import ray
from ray.actor import ActorHandle
from ray.serve.handle import DeploymentHandle

_PAGES_PER_BATCH = 8
_MAX_PENDING_TASKS = 16


@ray.remote(name="BackgroundParsingActor", max_concurrency=16)
class BackgroundParsingActor:
    """
    This actor handles the document parsing in the background.
    It's a singleton actor with limited concurrency.
    """

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

        parser_params = parser_params or {}

        try:
            actor_logger.info(
                f"BackgroundParsingActor: Start parsing for job_id: {job_id}, file: {filename}"
            )
            start_time = time.time()
            result = await parser_deployment_handle.parse.remote(
                data=document_content_bytes,
                filename=filename,
                formula_enable=parser_params.get("formula_enable", True),
                table_enable=parser_params.get("table_enable", True),
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

    async def parse_document_parallel(
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

        parser_params = parser_params or {}

        from .mineru_parser import MinerUParser

        # Instantiate MinerUParser locally within this replica to do the work.
        mineru = MinerUParser()

        actor_logger.info(
            f"BackgroundParsingActor: Start PARALLEL parsing for job_id: {job_id}, file: {filename}"
        )
        overall_start_time = time.time()

        try:
            # 1. Sanitize the PDF once to get the total page count and cleaned PDF bytes.
            # Ray will automatically place the returned bytes into the object store and pass
            # it by reference to subsequent remote calls, avoiding repeated data transfer.
            sanitized_pdf_bytes, page_count = mineru.sanitize_pdf(
                document_content_bytes, filename
            )
            document_content_bytes = None  # Release original memory
            actor_logger.info(f"Job {job_id}: Sanitized PDF, total pages: {page_count}")

            # 2. Submit parsing tasks in batches, using ray.wait() to control concurrency.
            batch_size = int(
                os.getenv("PARALLEL_PARSING_BATCH_SIZE", str(_PAGES_PER_BATCH))
            )
            pending_refs = []
            all_refs = []
            for i in range(0, page_count, batch_size):
                start_page_idx = i
                end_page_idx = min(i + batch_size - 1, page_count - 1)
                ref = parser_deployment_handle.parse.remote(
                    data=sanitized_pdf_bytes,
                    filename=filename,
                    start_page_idx=start_page_idx,
                    end_page_idx=end_page_idx,
                    formula_enable=parser_params.get("formula_enable", True),
                    table_enable=parser_params.get("table_enable", True),
                )
                pending_refs.append(ref)
                all_refs.append(ref)

                if len(pending_refs) >= _MAX_PENDING_TASKS:
                    actor_logger.info(
                        f"Job {job_id}: Reached max pending tasks ({len(pending_refs)}). Waiting for one to complete..."
                    )
                    # Use asyncio.wait with FIRST_COMPLETED to wait for at least one task to finish.
                    # This is non-blocking and integrates with the actor's event loop.
                    _done_refs, pending_refs_set = await asyncio.wait(
                        pending_refs, return_when=asyncio.FIRST_COMPLETED
                    )
                    pending_refs = list(pending_refs_set)

            # 3. Wait for all tasks to complete and gather the partial results.
            actor_logger.info(
                f"Job {job_id}: All {len(all_refs)} batches submitted. Gathering results..."
            )
            partial_results = await asyncio.gather(*all_refs)
            actor_logger.info(f"Job {job_id}: All parsing batches completed.")

            # 4. Merge the partial results into a final result on a parser replica.
            final_result = mineru.merge_partial_results(
                sanitized_pdf_bytes,
                partial_results,
            )
            # Do not return ParseResult object directly.
            final_result = asdict(final_result)

            end_time = time.time()
            parsing_duration = end_time - overall_start_time
            actor_logger.info(
                f"BackgroundParsingActor: Total parallel parsing for job_id: {job_id} (file: {filename}) took {parsing_duration:.2f} seconds."
            )
            await state_manager_actor_handle.store_job_result.remote(
                job_id, final_result
            )
            actor_logger.info(
                f"BackgroundParsingActor: Parallel parsing successful for job_id: {job_id}."
            )
        except Exception as e:
            error_msg = f"BackgroundParsingActor: Error during parallel parsing for job_id {job_id} (file: {filename}): {str(e)}"
            actor_logger.error(error_msg, exc_info=True)
            await state_manager_actor_handle.update_job_status.remote(
                job_id, "failed", error_message=error_msg
            )
