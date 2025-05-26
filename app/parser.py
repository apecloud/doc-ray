import asyncio
import logging
import os
from typing import Any, Dict, Optional, Union

import ray
from ray import serve

from app.mineru_parser import MinerUParser, ParseResult

logger = logging.getLogger(__name__)


@serve.deployment(
    name="DocumentParserDeployment",  # Assign a name to the deployment
    num_replicas=int(
        os.getenv("NUM_PARSER_REPLICAS", "1")
    ),  # Default to 2 replicas, configurable via env var
    # Example: Define resource requirements for each replica
    # ray_actor_options={"num_cpus": 1, "num_gpus": 0.25 if you need GPUs}
)
class DocumentParser:
    def __init__(self):
        # In a real application, this might load models or other resources.
        logging.basicConfig(level=logging.INFO)
        logger.info("DocumentParser initialized.")

    async def parse(
        self,
        document_object_ref: Union[ray.ObjectRef, bytes],
        filename: str,
        job_id: str,
        parser_type: Optional[str] = None,
        parser_params: Optional[Dict[str, Any]] = None,
    ) -> ParseResult:
        # Retrieve the actual document content from the ObjectRef
        logger.info(
            f"Job ID {job_id}: Received document_object_ref of type: {type(document_object_ref)}"
        )
        if isinstance(document_object_ref, ray.ObjectRef):
            logger.info(f"Job ID {job_id}: Resolving ObjectRef...")
            document_content_bytes: bytes = await document_object_ref
        elif isinstance(document_object_ref, bytes):
            logger.info(f"Job ID {job_id}: Received bytes directly.")
            document_content_bytes: bytes = document_object_ref
        else:
            # This case should ideally not happen if the calling chain is correct
            logger.error(
                f"Job ID {job_id}: Unexpected type for document_object_ref: {type(document_object_ref)}"
            )
            raise TypeError(
                f"Expected ray.ObjectRef or bytes, got {type(document_object_ref)}"
            )

        logger.info(f"Starting parsing for job_id: {job_id} with file {filename}")

        # Run the potentially blocking MinerUParser().parse() in a separate thread
        loop = asyncio.get_running_loop()
        try:
            mineru = MinerUParser()

            # The result from mineru.parse is a ParseResult object.
            # We need to decide what string representation to return.
            # For this example, let's assume we want to return the markdown content.
            parse_result_obj = await loop.run_in_executor(
                None,  # Use default ThreadPoolExecutor
                mineru.parse,  # The blocking function
                document_content_bytes,
                filename,
            )
            logger.info(f"Completed parsing for job_id: {job_id}")
            return parse_result_obj
        except Exception as e:
            logger.error(
                f"Job ID {job_id}: Error during MinerUParser execution: {e}",
                exc_info=True,
            )
            # Re-raise or return an error message string
            raise
