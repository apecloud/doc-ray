import asyncio
import logging
import os
from dataclasses import asdict

from ray import serve

logger = logging.getLogger(__name__)
deployment_name = "DocumentParserDeployment"


@serve.deployment(
    name=deployment_name,
    num_replicas=int(os.getenv("PARSER_NUM_REPLICAS", "1")),
    # Example: Define resource requirements for each replica
    # ray_actor_options={"num_cpus": 1, "num_gpus": 0.25 if you need GPUs}
)
class DocumentParser:
    def __init__(self):
        logging.basicConfig(level=logging.INFO)

        from .mineru_parser import MinerUParser

        # Instantiate MinerUParser locally within this replica to do the work.
        # It doesn't need a parser_deployment_handle because it's the worker, not an orchestrator.
        self.mineru = MinerUParser()

        import torch

        logger.info(f"os.cpu_count(): {os.cpu_count()}")
        logger.info(
            f"PyTorch threads: {torch.get_num_threads()}, interop threads: {torch.get_num_interop_threads()}"
        )
        logger.info(
            f"PyTorch CUDA available: {torch.cuda.is_available()}, device count: {torch.cuda.device_count()}"
        )
        logger.info(
            f"PyTorch MPS available: {torch.mps.is_available()}, device count: {torch.mps.device_count()}"
        )
        logger.info("DocumentParser initialized.")

    async def parse(self, data: bytes, filename: str) -> dict:
        replica_context = serve.get_replica_context()
        logger.info(f"Replica {replica_context.replica_id} received parse request.")

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,  # Use default ThreadPoolExecutor
                self.mineru.parse,  # Call the method on the instance
                data,
                filename,
            )
            logger.info(f"Replica {replica_context.replica_id} completed parse.")

            # Do not return a ParseResult object directly. When Ray serializes this object,
            # it will package the entire mineru_parser module,
            # significantly increasing the memory overhead of the calling actor.
            return asdict(result)
        except Exception as e:
            logger.error(
                f"Replica {replica_context.replica_id}: Error during parse: {e}",
                exc_info=True,
            )
            raise

    # async def batch_image_analyze(
    #     self,
    #     images_with_extra_info: list[tuple[np.ndarray, bool, str]],
    #     ocr: bool,
    #     show_log: bool = False,
    #     layout_model=None,
    #     formula_enable=None,
    #     table_enable=None,
    # ) -> list:
    #     """
    #     Remotely callable method to process a batch of images.
    #     This method will be executed by a DocumentParser replica.
    #     """
    #     replica_context = serve.get_replica_context()
    #     logger.info(
    #         f"Replica {replica_context.replica_id} received batch_image_analyze "
    #         f"request with {len(images_with_extra_info)} images."
    #     )

    #     loop = asyncio.get_running_loop()
    #     try:
    #         # MinerUParser.batch_image_analyze calls may_batch_image_analyze, which is CPU/GPU intensive.
    #         # Run it in an executor to avoid blocking the replica's asyncio event loop.
    #         result_list = await loop.run_in_executor(
    #             None,  # Use default ThreadPoolExecutor
    #             self.mineru.batch_image_analyze,  # Call the method on the instance
    #             images_with_extra_info,
    #             ocr,
    #             show_log,
    #             layout_model,
    #             formula_enable,
    #             table_enable,
    #         )
    #         logger.info(
    #             f"Replica {replica_context.replica_id} completed batch_image_analyze for {len(images_with_extra_info)} images."
    #         )
    #         return result_list
    #     except Exception as e:
    #         logger.error(
    #             f"Replica {replica_context.replica_id}: Error during batch_image_analyze: {e}",
    #             exc_info=True,
    #         )
    #         raise

    # async def ocr(
    #     self,
    #     img: np.ndarray | list | str | bytes,
    #     det: bool = True,
    #     rec: bool = True,
    #     mfd_res: list | None = None,
    #     tqdm_enable: bool = False,
    # ) -> list:
    #     """
    #     Remotely callable method to perform OCR on a batch of images.
    #     This method will be executed by a DocumentParser replica.
    #     """
    #     replica_context = serve.get_replica_context()
    #     logger.info(f"Replica {replica_context.replica_id} received ocr request.")

    #     loop = asyncio.get_running_loop()
    #     try:
    #         # MinerUParser.ocr can handle a list of images.
    #         ocr_results = await loop.run_in_executor(
    #             None,  # Use default ThreadPoolExecutor
    #             self.mineru.ocr,  # Call the method on the instance
    #             img,
    #             det,
    #             rec,
    #             mfd_res,
    #             tqdm_enable,
    #         )
    #         logger.info(f"Replica {replica_context.replica_id} completed ocr.")
    #         return ocr_results  # This should be a list of OCR results, one for each image in images_batch
    #     except Exception as e:
    #         logger.error(
    #             f"Replica {replica_context.replica_id}: Error during ocr: {e}",
    #             exc_info=True,
    #         )
    #         raise
