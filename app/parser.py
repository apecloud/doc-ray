import asyncio
import logging
import os
from dataclasses import asdict

from ray import serve

logger = logging.getLogger(__name__)
deployment_name = "DocumentParserDeployment"


@serve.deployment(
    name=deployment_name,
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

    async def parse(
        self,
        data: bytes,
        filename: str,
        start_page_idx=None,
        end_page_idx=None,
        formula_enable=True,
        table_enable=True,
    ) -> dict:
        replica_context = serve.get_replica_context()
        logger.info(f"Replica {replica_context.replica_id} received parse request.")

        try:
            result = self.mineru.parse(
                data,
                filename,
                start_page_idx,
                end_page_idx,
                formula_enable,
                table_enable,
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
