import logging

import ray
from ray import serve
from ray.serve.config import HTTPOptions, ProxyLocation

# Import the application entrypoint from your app module
# This assumes your app.main defines 'entrypoint' which is the ServeController.bind() result
from app.main import entrypoint as doc_parser_app_entrypoint

# Also need the state manager if we want to ensure it's up, though main.py handles its creation.
# from app.state_manager import JobStateManager

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    ray_initialized_by_script = False
    # Initialize Ray
    # In a production cluster, Ray might already be running.
    # 'address="auto"' connects to an existing cluster.
    # For local development, ray.init() starts a new Ray instance.
    if not ray.is_initialized():
        # Set dashboard port to avoid conflicts if multiple Ray instances run locally.
        # Disable usage stats for cleaner logs during development.
        # Include JobStateManager and DocumentParser code in the runtime environment for the actors
        # This is important if these modules are not part of the default Python path for Ray workers.
        # However, uv should make them available if run from the project root.
        runtime_env = {
            "working_dir": ".",  # Assumes run.py is in doc_parser_service directory
            "py_modules": [],  # Can list specific modules if needed, but working_dir often covers it.
            # "pip": ["fastapi", "ray[serve]"] # Dependencies, uv handles this outside typically
        }
        try:
            ray.init(
                logging_level=logging.WARNING,  # Ray's own logging, not app logging
                configure_logging=True,
                # runtime_env=runtime_env, # Usually not needed if using uv venv and running from project root
                dashboard_host="0.0.0.0",  # Make dashboard accessible
                # dashboard_port=8265, # Default, change if needed
                # num_cpus=4, # Limit resources for local testing if desired
                # object_store_memory=10**9 # 1GB, example
            )
            logger.info("Ray initialized successfully.")
            ray_initialized_by_script = True
        except Exception as e:
            logger.error(f"Failed to initialize Ray: {e}")
            return
    else:
        logger.info("Ray is already initialized.")

    try:
        logger.info("Starting Ray Serve...")
        # Configure HTTP options for Ray Serve, including host, port, and proxy location.
        # ProxyLocation.EveryNode means an HTTP proxy will run on every node with a Serve replica.
        # For local testing, this typically means one proxy on your machine.
        serve.start(
            proxy_location=ProxyLocation.EveryNode,
            http_options=HTTPOptions(host="0.0.0.0", port=8000),
        )

        logger.info("Deploying the application on Ray Serve...")
        serve.run(
            doc_parser_app_entrypoint,
            name="doc_parser",  # Application name, should match one in config if using one
            route_prefix="/",  # Serve at the root
            blocking=True,  # This will block until Ctrl+C, so the script doesn't exit immediately
        )
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Initiating shutdown sequence...")
    except Exception as e:
        logger.error(f"Error running Ray Serve: {e}", exc_info=True)
    finally:
        # Attempt to delete the specific application deployed by this script
        app_name_to_delete = "doc_parser"
        try:
            # Check if Serve is running and the app exists before attempting to delete.
            # serve.status() will return an empty status if Serve isn't running,
            # so applications.get() will safely return None.
            serve_status = serve.status()
            if serve_status.applications.get(app_name_to_delete):
                logger.info(f"Deleting Ray Serve application '{app_name_to_delete}'...")
                serve.delete(app_name_to_delete) # _blocking=True is the default
                logger.info(f"Ray Serve application '{app_name_to_delete}' deleted successfully.")
            else:
                logger.info(f"Ray Serve application '{app_name_to_delete}' not found or not running. No specific app to delete.")
        except Exception as e:
            # This catch is for errors during the serve.status() or serve.delete() calls
            logger.error(f"Error during Ray Serve cleanup for application '{app_name_to_delete}': {e}", exc_info=True)

        # Explicitly kill the DocRayJobStateManagerActor if it exists
        if ray.is_initialized(): # Ensure Ray is still initialized before trying to get actor
            try:
                state_manager_actor_to_kill = ray.get_actor("DocRayJobStateManagerActor")
                ray.kill(state_manager_actor_to_kill)
                logger.info("DocRayJobStateManagerActor killed successfully during shutdown.")
            except ValueError:
                logger.info("DocRayJobStateManagerActor not found during shutdown, no need to kill.")
            except Exception as e:
                logger.error(f"Error killing DocRayJobStateManagerActor during shutdown: {e}", exc_info=True)

        if ray_initialized_by_script and ray.is_initialized():
            logger.info("Ray was initialized by this script. Shutting down Ray.")
            ray.shutdown()
            logger.info("Ray shutdown complete.")
        elif ray.is_initialized():
            logger.info("Ray was not initialized by this script; not shutting down Ray itself.")


if __name__ == "__main__":
    # This script is intended to be run from the `doc_parser_service` directory.
    # Ensure the app directory is in PYTHONPATH, which it should be if running from root.
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # sys.path.insert(0, current_dir) # If app is not found, this might be needed.

    logger.info("Starting document parsing service...")
    main()
