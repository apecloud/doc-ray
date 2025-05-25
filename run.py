import ray
from ray import serve
import logging
import os

# Import the application entrypoint from your app module
# This assumes your app.main defines 'entrypoint' which is the ServeController.bind() result
from app.main import entrypoint as doc_parser_app_entrypoint
# Also need the state manager if we want to ensure it's up, though main.py handles its creation.
# from app.state_manager import JobStateManager 

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
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
            "working_dir": ".", # Assumes run.py is in doc_parser_service directory
            "py_modules": [], # Can list specific modules if needed, but working_dir often covers it.
            # "pip": ["fastapi", "ray[serve]"] # Dependencies, uv handles this outside typically
        }
        try:
            ray.init(
                logging_level=logging.WARNING, # Ray's own logging, not app logging
                configure_logging=True,
                # runtime_env=runtime_env, # Usually not needed if using uv venv and running from project root
                dashboard_host="0.0.0.0", # Make dashboard accessible
                # dashboard_port=8265, # Default, change if needed
                # num_cpus=4, # Limit resources for local testing if desired
                # object_store_memory=10**9 # 1GB, example
            )
            logger.info("Ray initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Ray: {e}")
            return
    else:
        logger.info("Ray is already initialized.")

    # Start Ray Serve
    # serve.start() is an older API, serve.run() is preferred for deploying applications.
    # serve.run() takes the application built from `ServeController.bind(...)`
    # The host and port here are for the Ray Serve HTTP proxy.
    try:
        logger.info("Starting Ray Serve and deploying the application...")
        serve.run(
            doc_parser_app_entrypoint, 
            name="doc_parser", # Application name, should match one in config if using one
            route_prefix="/", # Serve at the root
            host="0.0.0.0", # Accessible externally
            port=8000
        )
        logger.info("Ray Serve is running with the application deployed. Access at http://0.0.0.0:8000")
        # Keep the script running until manually stopped (e.g., Ctrl+C)
        import time
        while True:
            time.sleep(60) # Keep main thread alive
            logger.debug("Service still running...")
    except KeyboardInterrupt:
        logger.info("Shutting down Ray Serve...")
    except Exception as e:
        logger.error(f"Error running Ray Serve: {e}", exc_info=True)
    finally:
        logger.info("Stopping Ray Serve.")
        serve.shutdown()
        logger.info("Ray Serve shutdown complete.")
        if ray.is_initialized():
            ray.shutdown()
            logger.info("Ray shutdown complete.")

if __name__ == "__main__":
    # This script is intended to be run from the `doc_parser_service` directory.
    # Ensure the app directory is in PYTHONPATH, which it should be if running from root.
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # sys.path.insert(0, current_dir) # If app is not found, this might be needed.
    
    logger.info("Starting document parsing service...")
    main()
