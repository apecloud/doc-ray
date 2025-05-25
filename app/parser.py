import asyncio
import logging
import os  # Added for os.getenv
from typing import (
    Any,
    Dict,
    Optional,
    Union,  # Added Union for type hinting
)

import ray  # Added for @ray.remote and ObjectRef
from ray import serve  # Added for serve.deployment

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@serve.deployment(
    name="DocumentParserDeployment", # Assign a name to the deployment
    num_replicas=int(os.getenv("NUM_PARSER_REPLICAS", "1")),  # Default to 2 replicas, configurable via env var
    # Example: Define resource requirements for each replica
    # ray_actor_options={"num_cpus": 1, "num_gpus": 0.25 if you need GPUs}
)
class DocumentParser:
    def __init__(self):
        # In a real application, this might load models or other resources.
        logger.info("DocumentParser initialized.")

    async def parse(
        self,
        document_object_ref: Union[ray.ObjectRef, bytes],
        job_id: str,
        parser_type: Optional[str] = None,
        parser_params: Optional[Dict[str, Any]] = None,
        file_content_type: Optional[str] = None, # Added content_type
    ) -> str:
        '''
        Simulates a time-consuming document parsing process.
        In a real application, this would involve actual parsing logic,
        potentially using parser_type, parser_params, and file_content_type.
        '''
        # Retrieve the actual document content from the ObjectRef
        logger.info(f"Job ID {job_id}: Received document_object_ref of type: {type(document_object_ref)}")
        if isinstance(document_object_ref, ray.ObjectRef):
            logger.info(f"Job ID {job_id}: Resolving ObjectRef...")
            document_content_bytes: bytes = await document_object_ref
        elif isinstance(document_object_ref, bytes):
            logger.info(f"Job ID {job_id}: Received bytes directly.")
            document_content_bytes: bytes = document_object_ref
        else:
            # This case should ideally not happen if the calling chain is correct
            logger.error(f"Job ID {job_id}: Unexpected type for document_object_ref: {type(document_object_ref)}")
            raise TypeError(f"Expected ray.ObjectRef or bytes, got {type(document_object_ref)}")

        document_data_for_log = ""
        if file_content_type and file_content_type.startswith("text/"):
            try:
                document_data_str = document_content_bytes.decode("utf-8", errors="ignore")
                document_data_for_log = document_data_str[:50] # Log first 50 chars
            except UnicodeDecodeError:
                document_data_for_log = f"[binary data {len(document_content_bytes)} bytes, not UTF-8 decodable]"
        else:
            document_data_for_log = f"[binary data {len(document_content_bytes)} bytes, type: {file_content_type}]"

        logger.info(
            f"Starting parsing for job_id: {job_id} with data from ObjectRef: '{document_data_for_log}...', "
            f"parser_type: {parser_type}, parser_params: {parser_params}, content_type: {file_content_type}"
        )

        # Simulate a delay (e.g., 5 to 15 seconds)
        processing_time = 10 # Simulate 10 seconds of processing
        await asyncio.sleep(processing_time) # Use asyncio.sleep for async compatibility

        # Example of using parser_type and parser_params
        if parser_type == "special_text_parser":
            param_value = parser_params.get("some_param", "default_value") if parser_params else "default_value"
            # Actual parsing logic would use document_content_bytes here
            document_data_str = document_content_bytes.decode("utf-8", errors="ignore") # Example decode
            parsed_content = (
                f"Successfully parsed (using {parser_type} with param: {param_value}) "
                f"content for job_id: {job_id} - Original data snippet: '{document_data_str[:50]}...'"
            )
        else: # Default parser
            # Actual parsing logic would use document_content_bytes here
            parsed_content = (
                f"Successfully processed (using default parser) content for job_id: {job_id}. "
                f"Data size: {len(document_content_bytes)} bytes. Content-Type: {file_content_type}."
            )

        logger.info(f"Completed parsing for job_id: {job_id}")

        return parsed_content

# Example of how to test this parser (optional, for direct testing)
if __name__ == "__main__":
    # To test a Serve deployment, you would typically deploy it using `serve.run`
    # and send requests to it. Direct instantiation and calling methods is for
    # unit testing the class logic itself, not its behavior as a deployment.
    #
    # async def main_test_logic():
    #     # This part tests the class logic, not as a Ray actor/deployment
    #     parser_logic_test = DocumentParser()
    #     test_data = "This is a test document."
    #     test_bytes = test_data.encode('utf-8')
    #     test_job_id = "test_job_001"
    #
    #     # To test with Ray ObjectRef (locally, not as a remote call to actor):
    #     if not ray.is_initialized():
    #         ray.init()
    #     obj_ref = ray.put(test_bytes)
    #
    #     print(f"Submitting document for parsing (job_id: {test_job_id})...")
    #     result = await parser_logic_test.parse(
    #         obj_ref, test_job_id, parser_type="default", file_content_type="text/plain"
    #     )
    #     print(f"Parsing result: {result}")
    #     ray.shutdown()
    # asyncio.run(main_test_logic())
    logger.info("To test DocumentParser as a deployment, please use `serve run` or `serve deploy`.")
