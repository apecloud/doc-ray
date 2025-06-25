#!/usr/bin/env python3

import argparse
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
DOCRAY_HOST = os.environ.get("DOCRAY_HOST", "http://localhost:8639")


def parse_file(path: Path, parser_params: str = "{}"):
    if not DOCRAY_HOST:
        raise ValueError("DOCRAY_HOST environment variable is not set.")

    job_id = None
    try:
        # Data payload for the form, including parser parameters
        data = {"parser_params": parser_params}

        # Submit file to doc-ray
        if isinstance(path, Path):
            with open(path, "rb") as f:
                files = {"file": (path.name, f)}
                response = requests.post(f"{DOCRAY_HOST}/submit", files=files, data=data)
        else:  # Assume it's a URL (string), path is actually a string here
            url_path = str(path)  # Keep original for requests.get
            parsed_url = urlparse(url_path)
            filename = os.path.basename(parsed_url.path)
            if not filename:  # Handle cases like "http://example.com"
                filename = "downloaded_file"

            with requests.get(url_path, stream=True) as r:
                r.raise_for_status()  # Check if the download was successful
                files = {"file": (filename, r.raw)}
                response = requests.post(f"{DOCRAY_HOST}/submit", files=files, data=data)

        try:
            response.raise_for_status()
        except:
            logger.error(f"submit failed with status code {response.status_code} and content: {response.text}")
            raise
        submit_response = response.json()
        job_id = submit_response["job_id"]
        # Use filename if path is URL, otherwise path.name
        submitted_filename = filename if not isinstance(path, Path) else path.name
        logger.info(f"Submitted file {submitted_filename} to DocRay, job_id: {job_id}")

        begin_time = time.time()

        # Initialize last_log_time to the begin_time to avoid logging immediately
        last_log_time = begin_time

        # Polling the processing status
        while True:
            time.sleep(5)  # Poll every 5 seconds
            status_response: dict = requests.get(
                f"{DOCRAY_HOST}/status/{job_id}"
            ).json()
            current_time = time.time()
            elapsed_time = current_time - begin_time
            status = status_response["status"]

            if status == "completed":
                break
            elif status == "failed":
                error_message = status_response.get("error", "Unknown error")
                raise RuntimeError(
                    f"DocRay parsing failed for job {job_id}: {error_message}"
                )
            elif status not in ["processing"]:
                raise RuntimeError(
                    f"Unexpected DocRay job status for {job_id}: {status}"
                )

            # Log every 30 seconds
            if current_time - last_log_time >= 30:
                logger.info(
                    f"DocRay job {job_id} is still processing after {elapsed_time:.0f} seconds."
                )
                last_log_time = current_time

        # Get the result
        result_response = requests.get(f"{DOCRAY_HOST}/result/{job_id}").json()
        result = result_response["result"]
        middle_json = result["middle_json"]
        images_data = result.get("images", {})

        end_time = time.time()
        logger.info(
            f"DocRay parsing completed for job {job_id}, spent: {end_time - begin_time}s, middle_json: {len(middle_json)}, images: {len(images_data)}"
        )
        print("\nMarkdown Content:\n")
        print(result["markdown"])
        return result

    except requests.exceptions.RequestException:
        logger.exception("DocRay API request failed")
        raise
    except Exception:
        logger.exception("DocRay parsing failed")
        raise
    finally:
        # Delete the job in doc-ray to release resources
        if job_id:
            try:
                requests.delete(f"{DOCRAY_HOST}/result/{job_id}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Failed to delete DocRay job {job_id}: {e}")


def main():
    parser = argparse.ArgumentParser(description="DocRay client to parse a file.")
    parser.add_argument(
        "input_path",
        type=str,
        help="The path to the local file or the URL of the file to be parsed.",
    )
    parser.add_argument(
        "--parser-params",
        type=str,
        default="{}",
        help="A JSON string of parameters for the parser, e.g., \'{\"formula_enable\": false}\'.",
    )
    parser.add_argument(
        "--dump-middle-json",
        action="store_true",
        help="Dump the middle JSON result",
    )
    args = parser.parse_args()

    input_path: str = args.input_path

    if input_path.startswith("http://") or input_path.startswith("https://"):
        logger.info(f"Input is a URL: {input_path}")
    else:
        input_path = Path(input_path)  # Convert to Path object if it's a local path
        if not input_path.is_file():
            logger.error(f"The file '{input_path}' does not exist or is not a file.")
            return
        logger.info(f"Input is a local file: {input_path}")

    try:
        logger.info(f"Starting to parse file: {input_path}")
        result = parse_file(input_path, args.parser_params)
        if args.dump_middle_json:
            print("\nMiddle JSON:\n")
            print(result["middle_json"])
    except Exception as e:
        logger.error(
            f"An error occurred during the processing of '{input_path}'.", exc_info=True
        )


if __name__ == "__main__":
    main()
