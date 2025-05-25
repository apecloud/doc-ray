import time
import logging
import asyncio # Added as per instruction

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DocumentParser:
    def __init__(self):
        # In a real application, this might load models or other resources.
        logger.info("DocumentParser initialized.")

    async def parse(self, document_data: str, job_id: str) -> str:
        '''
        Simulates a time-consuming document parsing process.
        In a real application, this would involve actual parsing logic.
        '''
        logger.info(f"Starting parsing for job_id: {job_id} with data: '{document_data[:30]}...'")
        
        # Simulate a delay (e.g., 5 to 15 seconds)
        processing_time = 10 # Simulate 10 seconds of processing
        await asyncio.sleep(processing_time) # Use asyncio.sleep for async compatibility
        
        parsed_content = f"Successfully parsed content for job_id: {job_id} - Original data: '{document_data}'"
        logger.info(f"Completed parsing for job_id: {job_id}")
        
        return parsed_content

    async def parse_sync_wrapper(self, document_data: str, job_id: str) -> str:
        '''
        A wrapper to call the async parse method.
        This can be used if the caller is not directly awaiting the parse method,
        allowing the parse method itself to be async.
        '''
        # This wrapper might seem redundant if the caller is already async.
        # However, if we had a synchronous parsing library, we might run it
        # in a thread pool executor here. For now, it directly calls the async method.
        # If the DocumentParser.parse was synchronous, we'd use:
        # loop = asyncio.get_event_loop()
        # await loop.run_in_executor(None, self.parse_synchronous_function, document_data, job_id)
        return await self.parse(document_data, job_id)

# Example of how to test this parser (optional, for direct testing)
if __name__ == "__main__":
    import asyncio
    async def main():
        parser = DocumentParser()
        test_data = "This is a test document."
        test_job_id = "test_job_001"
        print(f"Submitting document for parsing (job_id: {test_job_id})...")
        result = await parser.parse(test_data, test_job_id)
        print(f"Parsing result: {result}")

    # Add asyncio.sleep to the main function to test the async nature of the parser
    # This is not needed when running in Ray Serve
    # asyncio.run(main())
