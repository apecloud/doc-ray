import logging
import os
from typing import Any, Dict

import ray
from ray.serve.config import AutoscalingConfig

# Import project components
from .background_parsing import BackgroundParsingActor
from .parser import DocumentParser
from .server import ServeController, app
from .state_manager import (  # Actor
    DEFAULT_JOB_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_JOB_TTL_SECONDS,
    JobStateManager,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def is_standalone_mode() -> bool:
    return os.environ.get("STANDALONE_MODE", "false").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


# Helper function to determine GPU availability for Ray actors
def get_ray_actor_options_based_on_gpu_availability(
    default_gpus_per_replica: float = 0.0, gpus_if_available: float = 1.0
) -> Dict[str, Any]:
    """
    Determines Ray actor options, specifically num_gpus, based on cluster GPU availability.
    This function should be called after ray.init() or when Ray is connected.
    """
    ray_actor_options = {
        "num_cpus": int(os.getenv("PARSER_NUM_CPUS_PER_REPLICA", "0")),
        "memory": int(
            os.getenv("PARSER_MEMORY_PER_REPLICA", str(5 * 1024 * 1024 * 1024))
        ),  # Default 5GB
    }

    if is_standalone_mode():
        # Ignore memory requirement for standalone mode
        del ray_actor_options["memory"]

    # Allow overriding via environment variable for explicit control
    # PARSER_FORCE_GPU_PER_REPLICA: "0", "0.25", "1"
    force_gpu_str = os.getenv("PARSER_FORCE_GPU_PER_REPLICA")
    if force_gpu_str is not None:
        try:
            forced_gpus = float(force_gpu_str)
            logger.info(
                f"PARSER_FORCE_GPU_PER_REPLICA is set to {forced_gpus}. Using this value for num_gpus."
            )
            ray_actor_options["num_gpus"] = forced_gpus
            return ray_actor_options
        except ValueError:
            logger.warning(
                f"Invalid value for PARSER_FORCE_GPU_PER_REPLICA: '{force_gpu_str}'. Ignoring and proceeding with auto-detection."
            )

    if not ray.is_initialized():
        logger.warning(
            "Ray not initialized when trying to determine GPU availability for actor options. Defaulting num_gpus to %s.",
            default_gpus_per_replica,
        )
        ray_actor_options["num_gpus"] = default_gpus_per_replica
        return ray_actor_options

    if is_standalone_mode():
        nodes = ray.nodes()
        if len(nodes) == 0:
            logger.warning(
                "Ray reports no available nodes in standalone mode. Cannot determine CPU resources automatically."
            )
        else:
            # In standalone mode, there is typically only one node.
            node = nodes[0]
            cpus = node.get("Resources", {}).get("CPU", None)
            if cpus is None:
                logger.warning(
                    f"Cannot get CPU resources from node, node object: {node}"
                )
            else:
                ray_actor_options["num_cpus"] = cpus
                logger.info(
                    f"Assigned {cpus} CPUs for each replica in standalone mode."
                )

    try:
        cluster_gpus = ray.cluster_resources().get("GPU", 0)
        logger.info(f"Ray cluster reports {cluster_gpus} total GPUs.")
        if cluster_gpus > 0:
            logger.info(
                f"GPUs detected in the cluster. Requesting {gpus_if_available} GPU(s) per replica."
            )
            ray_actor_options["num_gpus"] = gpus_if_available
        else:
            logger.info(
                "No GPUs detected in the cluster. Replicas will run on CPU (num_gpus=0)."
            )
            ray_actor_options["num_gpus"] = 0.0
    except Exception as e:
        logger.error(
            f"Error checking Ray cluster GPU resources: {e}. Defaulting num_gpus to %s.",
            default_gpus_per_replica,
            exc_info=True,
        )
        ray_actor_options["num_gpus"] = default_gpus_per_replica

    return ray_actor_options


# Determine actor options before defining the deployment.
# This assumes ray.init() has been called or Ray is connected (e.g., by `serve run`).
# Request 1 full GPU per replica if any GPU is available in the cluster.
# If no GPUs, or if detection fails, it defaults to 0 GPUs.
_parser_actor_options = get_ray_actor_options_based_on_gpu_availability(
    default_gpus_per_replica=0.0, gpus_if_available=1.0
)
logger.info(f"Calculated ray_actor_options for DocumentParser: {_parser_actor_options}")


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
    state_manager_actor = JobStateManager.options(
        name="DocRayJobStateManagerActor", get_if_exists=True, lifetime="detached"
    ).remote(
        ttl_seconds=int(os.getenv("JOB_TTL_SECONDS", str(DEFAULT_JOB_TTL_SECONDS))),
        cleanup_interval_seconds=int(
            os.getenv(
                "JOB_CLEANUP_INTERVAL_SECONDS",
                str(DEFAULT_JOB_CLEANUP_INTERVAL_SECONDS),
            )
        ),
    )
    logger.info("DocRayJobStateManagerActor handle obtained/created.")
except Exception as e:
    logger.error(
        f"Failed to get or create DocRayJobStateManagerActor: {e}. This is critical."
    )
    # Depending on the setup, you might want to raise an exception here to stop deployment.
    # For now, we log and proceed, but the service will likely fail.
    state_manager_actor = None  # This will cause issues later if not handled

# Get or create the BackgroundParsingActor handle
try:
    background_parser_actor = BackgroundParsingActor.options(
        name="BackgroundParsingActor", get_if_exists=True, lifetime="detached"
    ).remote()
    logger.info("BackgroundParsingActor handle obtained/created.")
except Exception as e:
    logger.error(
        f"Failed to get or create BackgroundParsingActor: {e}. This is critical."
    )
    background_parser_actor = None

# 3. DocumentParser is now a Serve Deployment. It will be bound into the application.
#    The handle will be created by `DocumentParser.bind()` below.

# 4. Bind the ServeController with its dependencies to the application.
# This is what `serve run` will use based on the import path in the config file.
# (e.g., `doc_parser_service.app.main:app_builder` if we named this `app_builder`)
# Or, if the config points to `doc_parser_service.app.main:controller_app`, it will be this.
# For `serve.run(ServeController.bind(...))` in a Python script, this is how you'd do it.
if state_manager_actor and background_parser_actor:  # Ensure both actors are available
    # DocumentParser is imported from .parser
    # Its .bind() method creates a DeploymentHandle that ServeController will use.
    # Ray Serve will manage the lifecycle of DocumentParserDeployment.
    options = {}
    if is_standalone_mode():
        # In standalone mode, we assign all CPU resources of the host to the DocumentParser
        options["num_cpus"] = 0
    entrypoint = ServeController.options(ray_actor_options=options).bind(
        state_manager_actor_handle=state_manager_actor,
        background_parser_actor_handle=background_parser_actor,
        parser_deployment_handle=DocumentParser.options(
            ray_actor_options=_parser_actor_options,
            max_ongoing_requests=int(os.getenv("PARSER_MAX_ONGOING_REQUESTS", "1")),
            num_replicas=os.getenv("PARSER_NUM_REPLICAS", "auto"),
            autoscaling_config=AutoscalingConfig(
                initial_replicas=int(
                    os.getenv("PARSER_AUTOSCALING_INITIAL_REPLICAS", "1")
                ),
                min_replicas=int(os.getenv("PARSER_AUTOSCALING_MIN_REPLICAS", "1")),
                max_replicas=int(os.getenv("PARSER_AUTOSCALING_MAX_REPLICAS", "4")),
                target_ongoing_requests=int(
                    os.getenv("PARSER_AUTOSCALING_TARGET_ONGOING_REQUESTS", "5")
                ),
            ),
        ).bind(),  # Bind DocumentParser deployment
    )
    logger.info("ServeController bound with dependencies. Ready for serving.")
else:
    logger.critical(
        "One or more critical actors (JobStateManager or BackgroundParsingActor) "
        "are not available. Cannot bind ServeController."
    )

    # Fallback or error handling:
    # You might define a simple FastAPI app that returns an error if the critical actor is missing.
    # For now, this means `serve run` might fail if it tries to import `entrypoint` and it's not defined.
    # To make it runnable even in this error state (for debugging config issues):
    @app.get("/health")
    def health_check_error():
        return {
            "status": "error",
            "message": "Critical actor(s) could not be initialized.",
        }

    entrypoint = (
        app  # Serve the basic FastAPI app with an error message on health check
    )
    logger.info(
        "Serving a fallback FastAPI app due to critical actor initialization failure."
    )

# For local testing with `python doc_parser_service/app/main.py` (if you add `if __name__ == "__main__": serve.run(entrypoint)`)
# or when Ray Serve CLI picks up this file.
# The `serve run config.yaml` will typically look for an import path like `module:application_object`.
# So if your config.yaml has `import_path: doc_parser_service.app.main:entrypoint`,
# Ray Serve will import this file and use the `entrypoint` object created above.
