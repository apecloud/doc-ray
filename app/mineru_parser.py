import base64
import io
import json
import logging
import os
import shutil
import statistics
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path
from typing import Any

import fitz
import pypdfium2 as pdfium
import ray
from mineru.data.data_reader_writer import FileBasedDataWriter
from mineru.utils.enum_class import BlockType, MakeMode
from PIL import Image

from .const import IMAGE_EXTS, OFFICE_DOC_EXTS, PDF_EXT

if ray.is_initialized():
    logger = ray.logger
else:
    logger = logging.getLogger(__name__)

max_title_level = 8
image_dir_name = "images"
_monkey_patched = False
_orig_para_split = None


def _sanitize_string_for_utf8(s: str) -> str:
    if not isinstance(s, str):
        return s
    return s.encode("utf-8", "replace").decode("utf-8", "replace")


def _my_get_title_level(block):
    title_level = block.get("level", 1)
    if title_level > max_title_level:
        title_level = max_title_level
    elif title_level < 1:
        title_level = 0
    return title_level


def _my_para_split(page_info_list: list):
    # Do nothing
    return


def _monkey_patch_mineru():
    global _monkey_patched
    if _monkey_patched:
        return

    _monkey_patched = True

    import mineru.backend.pipeline.para_split as para_split
    import mineru.backend.pipeline.pipeline_middle_json_mkcontent as mkcontent

    # The original get_title_level only supports 4 levels, which is too small
    mkcontent.get_title_level = _my_get_title_level

    global _orig_para_split
    _orig_para_split = para_split.para_split
    para_split.para_split = _my_para_split


@dataclass
class ParseResult:
    markdown: str
    middle_json: str
    images: dict[str, str] = field(
        default_factory=dict
    )  # image name => base64-decoded image data


class MinerUParser:
    def __init__(self):
        self.prepared = False

        _monkey_patch_mineru()

    def _prepare(self):
        if self.prepared:
            return
        if not self._set_config_path():
            raise Exception("mineru.json not found")
        self.prepared = True

    def _set_config_path(self) -> bool:
        path = Path(os.getenv("MINERU_CONFIG_JSON", "./mineru.json"))
        if not path.exists():
            return False

        if os.getenv("MINERU_MODEL_SOURCE", None) is None:
            os.environ["MINERU_MODEL_SOURCE"] = "local"

        # Lazily import here because the module imports PyTorch.
        from mineru.utils import config_reader

        config_reader.CONFIG_FILE_NAME = str(path.absolute())
        return True

    def parse(
        self,
        data: bytes,
        filename: str,
        start_page_idx=None,
        end_page_idx=None,
        formula_enable=True,
        table_enable=True,
    ) -> ParseResult:
        self._prepare()

        # Lazily import these modules because they are slow to load
        from mineru.backend.pipeline.model_json_to_middle_json import (
            result_to_middle_json,
        )
        from mineru.backend.pipeline.pipeline_analyze import doc_analyze

        ok, pdf_data = to_pdf_bytes(data, filename, start_page_idx, end_page_idx)
        if not ok:
            raise Exception(f"Unsupported file format, filename: {filename}")

        temp_dir = os.environ.get("MINERU_TEMP_FILE_DIR", None)
        temp_dir_obj: tempfile.TemporaryDirectory | None = None
        if not temp_dir:
            temp_dir_obj = tempfile.TemporaryDirectory()
            temp_dir = temp_dir_obj.name

        try:
            disable_formula_and_table_recog = (
                os.getenv("MINERU_DISABLE_FORMULA_AND_TABLE_RECOGNITION", "0") == "1"
            )
            if disable_formula_and_table_recog:
                logger.info(
                    "Formula and table recognition has been disabled. To enable it, set the environment variable MINERU_DISABLE_FORMULA_AND_TABLE_RECOGNITION to 0"
                )
                formula_enable = False
                table_enable = False

            pdf_bytes_list = [pdf_data]
            lang_list = ["ch"]
            result = doc_analyze(
                pdf_bytes_list,
                lang_list,
                formula_enable=formula_enable,
                table_enable=table_enable,
            )

            infer_result = result[0][0]
            images_list = result[1][0]
            pdf_doc = result[2][0]
            _lang = result[3][0]
            _ocr_enable = result[4][0]

            local_image_dir = os.path.join(
                temp_dir, md5(filename.encode("utf-8")).hexdigest(), image_dir_name
            )
            os.makedirs(local_image_dir, exist_ok=True)
            image_writer = FileBasedDataWriter(local_image_dir)

            middle_json = result_to_middle_json(
                infer_result, images_list, pdf_doc, image_writer, _lang, _ocr_enable
            )
            if start_page_idx is None and end_page_idx is None:
                # Hack: do para_split() and other processing only when parsing the whole PDF file.
                global _orig_para_split
                if _orig_para_split is not None:
                    _orig_para_split(middle_json.get("pdf_info", []))
                adjust_title_level(pdf_data, middle_json)
                add_merged_text_field(middle_json)
            middle_json_str = json.dumps(middle_json, ensure_ascii=False)

            images_dict = {}
            if os.path.exists(local_image_dir) and os.listdir(local_image_dir):
                for root, _, files in os.walk(local_image_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        with open(file_path, "rb") as f:
                            name = f"images/{file}"
                            images_dict[name] = base64.b64encode(f.read()).decode()

            return ParseResult(
                markdown=self.middle_json_to_markdown(middle_json, image_dir_name),
                middle_json=_sanitize_string_for_utf8(middle_json_str),
                images=images_dict,
            )
        except:
            logger.exception("MinerUParser failed")
            raise
        finally:
            if temp_dir_obj is not None:
                temp_dir_obj.cleanup()

    def sanitize_pdf(self, data: bytes, filename: str) -> tuple[bytes, int]:
        ok, pdf_data = to_pdf_bytes(data, filename)
        if not ok:
            raise Exception(f"Unsupported file format, filename: {filename}")

        with fitz.open(stream=io.BytesIO(pdf_data)) as doc:
            return pdf_data, len(doc)

    def middle_json_to_markdown(self, middle_json: dict, image_dir="images") -> str:
        from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make

        pdf_info = middle_json["pdf_info"]
        markdown = union_make(pdf_info, MakeMode.MM_MD, image_dir)
        return _sanitize_string_for_utf8(markdown)

    def merge_partial_results(
        self, pdf_bytes: bytes, partial_results: list[dict]
    ) -> ParseResult:
        if not partial_results:
            return ParseResult()

        # 1. Merge images by combining dictionaries
        merged_images = {}
        for res in partial_results:
            merged_images.update(res.get("images", {}))

        # 2. Merge middle_json by combining their 'pdf_info' lists
        middle_json_dicts = [
            json.loads(res["middle_json"])
            for res in partial_results
            if res.get("middle_json")
        ]
        if not middle_json_dicts:
            return ParseResult()

        merged_middle_json_dict = merge_middle_jsons(middle_json_dicts)
        global _orig_para_split
        if _orig_para_split is not None:
            _orig_para_split(merged_middle_json_dict.get("pdf_info", []))

        adjust_title_level(pdf_bytes, merged_middle_json_dict)
        add_merged_text_field(merged_middle_json_dict)

        merged_middle_json_str = json.dumps(merged_middle_json_dict, ensure_ascii=False)

        # 3. Re-generate the final markdown from the merged middle_json
        merged_markdown = self.middle_json_to_markdown(merged_middle_json_dict)

        return ParseResult(
            markdown=merged_markdown,
            middle_json=_sanitize_string_for_utf8(merged_middle_json_str),
            images=merged_images,
        )


def merge_middle_jsons(middle_json_list: list[dict]) -> dict:
    if not middle_json_list:
        return {}
    # Use the first result as the base for metadata.
    merged_json = middle_json_list[0].copy()
    # The 'pdf_info' contains a list of pages. We will extend this list with pages from other results.
    merged_pdf_info: list = merged_json.get("pdf_info", [])

    for middle_json in middle_json_list[1:]:
        merged_pdf_info.extend(middle_json.get("pdf_info", []))

    # Rewrite page num.
    for idx, page_info in enumerate(merged_pdf_info):
        page_info["page_idx"] = idx
        para_blocks = page_info.get("para_blocks", [])
        for para_block in para_blocks:
            para_block["page_num"] = idx

    # Clear title level.
    for page_info in merged_pdf_info:
        para_blocks = page_info.get("para_blocks", [])
        for para_block in para_blocks:
            para_block.pop("level", None)

    # Call para_split()
    global _orig_para_split
    if _orig_para_split is not None:
        _orig_para_split(merged_pdf_info)

    merged_json["pdf_info"] = merged_pdf_info
    return merged_json


def add_merged_text_field(pipe_res: dict):
    # Lazily import merge_para_with_text here, to avoid initializing
    # pipeline_middle_json_mkcontent.latex_delimiters_config too early.
    # This is because it needs to read the configuration file, and here
    # it can be ensured that the configuration file has been set up.
    from mineru.backend.pipeline.pipeline_middle_json_mkcontent import (
        merge_para_with_text,
    )

    def _set_merged_text_field(block: dict[str, Any]):
        block["merged_text"] = merge_para_with_text(block)

    simple_block_types = set(
        [
            BlockType.TEXT,
            BlockType.LIST,
            BlockType.INDEX,
            BlockType.TITLE,
            BlockType.INTERLINE_EQUATION,
        ]
    )

    for page_info in pipe_res.get("pdf_info", []):
        paras_of_layout: list[dict[str, Any]] = page_info.get("para_blocks")
        if not paras_of_layout:
            continue
        for para_block in paras_of_layout:
            para_type = para_block["type"]
            if para_type in simple_block_types:
                _set_merged_text_field(para_block)
            elif para_type == BlockType.IMAGE:
                handle_block_types = [BlockType.IMAGE_CAPTION, BlockType.IMAGE_FOOTNOTE]
                for block in para_block["blocks"]:
                    if block["type"] in handle_block_types:
                        _set_merged_text_field(block)
            elif para_type == BlockType.TABLE:
                handle_block_types = [BlockType.TABLE_CAPTION, BlockType.TABLE_FOOTNOTE]
                for block in para_block["blocks"]:
                    if block["type"] in handle_block_types:
                        _set_merged_text_field(block)


def adjust_title_level(pdf_bytes: bytes | None, middle_json: dict):
    logger.info("Adjusting title level...")
    raw_text_blocks: dict[int, list[tuple[str, float]]] = {}
    if pdf_bytes is not None:
        raw_text_blocks = collect_all_text_blocks(pdf_bytes)
    title_blocks = []
    font_size_ratios = []
    for page_num, page_info in enumerate(middle_json.get("pdf_info", [])):
        paras_of_layout: list[dict[str, Any]] = page_info.get("para_blocks")
        if not paras_of_layout:
            continue
        raw_text_to_font_size = {}
        for raw_text_tup in raw_text_blocks.get(page_num, []):
            text = unicodedata.normalize("NFKC", raw_text_tup[0])
            raw_text_to_font_size[text] = raw_text_tup[1]
        for para_block in paras_of_layout:
            para_type = para_block["type"]
            if para_type != BlockType.TITLE:
                continue
            has_level = para_block.get("level", None)
            if has_level is not None:
                logger.info(
                    "MinerU has already set a title level; skipping adjustment."
                )
                return

            font_size = None
            bbox = None
            lines = para_block.get("lines", [])
            for line in lines:
                spans = line.get("spans", [])
                for span in spans:
                    content = span.get("content", "").strip()
                    content = unicodedata.normalize("NFKC", content)
                    font_size = raw_text_to_font_size.get(content, None)
                    bbox = span.get("bbox", None)
                    break
                break

            height = None
            if bbox is not None and len(bbox) == 4:
                height = bbox[3] - bbox[1]
            if font_size is not None and height is not None and height > 0:
                ratio = font_size / height
                font_size_ratios.append(ratio)

            title_blocks.append(
                [
                    font_size,
                    height,
                    para_block,
                ]
            )

    if len(title_blocks) == 0:
        return

    def assign_title_level(titles, delta=0.2):
        titles.sort(key=lambda x: x[0], reverse=True)
        level = 1
        prev_font_size = None
        max_level = max_title_level
        for title_block in titles:
            font_size = title_block[0]
            para_block = title_block[2]
            if prev_font_size is not None and prev_font_size - font_size > delta:
                level += 1
                if level > max_level:
                    level = max_level
            para_block["level"] = level
            prev_font_size = font_size

    # Assign title level base on the font size from the PDF
    title_blocks_with_font_size = list(filter(lambda x: x[0] is not None, title_blocks))
    assign_title_level(title_blocks_with_font_size)

    # If the font size cannot be obtained directly from the PDF document,
    # calculate an approximate font size based on the bounding box height.
    font_size_factor = statistics.median(font_size_ratios) if font_size_ratios else 1.0
    for title_block in title_blocks:
        if title_block[0] is None and title_block[1] is not None:
            title_block[0] = title_block[1] * font_size_factor

    # Filter out title blocks if its font size can't be determined.
    title_blocks = list(filter(lambda x: x[0] is not None, title_blocks))

    if len(title_blocks_with_font_size) == 0:
        # Assign title level base on the estimated font size
        assign_title_level(title_blocks, delta=2)
    else:
        # Align to the title block which has the closest font size,
        # or set to the next level if smaller than the last level
        min_font_size = min(title_blocks_with_font_size, key=lambda x: x[0])[0]
        deepest_title_block = max(
            title_blocks_with_font_size, key=lambda x: x[2]["level"]
        )
        deepest_level = deepest_title_block[2]["level"]
        if deepest_level > max_title_level:
            deepest_level = max_title_level
        delta = 2
        for title_block in title_blocks:
            # Only process those without assigned levels
            if "level" in title_block[2]:
                continue
            # The font size of the current title block is too small
            if title_block[0] < min_font_size - delta:
                title_block[2]["level"] = deepest_level
                continue

            # Align to the title block which has the closest font size
            closest_block = min(
                title_blocks_with_font_size, key=lambda x: abs(x[0] - title_block[0])
            )
            title_block[2]["level"] = closest_block[2]["level"]


def collect_all_text_blocks(pdf_bytes: bytes) -> dict[int, list[tuple[str, float]]]:
    try:
        with fitz.open(stream=io.BytesIO(pdf_bytes)) as doc:
            if not doc.is_pdf:
                return {}

            ret = {}
            for page_num, page in enumerate(doc):
                try:
                    # Extract text using 'dict' mode, which returns a dictionary structure
                    # containing detailed information: page -> block -> line -> span.
                    # Each span includes font size, content, etc.
                    page_data = page.get_text("dict")

                    blocks = page_data.get("blocks", [])
                    if not blocks:
                        continue

                    texts = []
                    # Iterate over all blocks in the page
                    for block in blocks:
                        # Check if the block is a text block (type 0) and contains line information
                        if block.get("type") == 0 and "lines" in block:
                            lines = block.get("lines", [])
                            # Iterate over all lines in the block
                            for line in lines:
                                spans = line.get("spans", [])
                                # Iterate over all spans in the line
                                for span in spans:
                                    font_size = span.get("size", 1.0)
                                    text_content = span.get("text", "").strip()
                                    if text_content:
                                        texts.append(
                                            (
                                                text_content,
                                                font_size,
                                            )
                                        )

                    ret[page_num] = texts
                except Exception:
                    logger.exception(
                        f"collect_all_text_blocks error processing page {page_num + 1}"
                    )

            return ret
    except Exception:
        logger.exception("collect_all_text_blocks failed")
        return {}


# # Extract texts from PDF by pdfium. But sometimes it can't correctly extract the font size.
# # Keep the code for reference.
# from mineru.utils.pdf_text_tool import get_page
# def collect_all_text_blocks(pdf_bytes: bytes) -> dict[int, list[tuple[str, float]]]:
#     pdf = None
#     try:
#         pdf = pdfium.PdfDocument(pdf_bytes)
#         ret = {}
#         for page_num, pdf_page in enumerate(pdf):
#             page_data = get_page(pdf_page)
#             texts = []
#             for block in page_data.get("blocks", []):
#                 for line in block.get("lines", []):
#                     for span in line.get("spans", []):
#                         font_size = span.get("font", {}).get("size", 1.0)
#                         text_content = span.get("text", "").strip()
#                         if text_content:
#                             texts.append(
#                                 (
#                                     text_content,
#                                     font_size,
#                                 )
#                             )
#             if len(texts) > 0:
#                 ret[page_num] = texts
#         return ret
#     except Exception:
#         logger.exception("collect_all_text_blocks failed")
#         return {}
#     finally:
#         if pdf is not None:
#             pdf.close()


def to_pdf_bytes(
    data: bytes, filename: str, start_page_idx=None, end_page_idx=None
) -> tuple[bool, bytes]:
    ext = Path(filename).suffix.lower()
    if ext == PDF_EXT:
        return True, sanitize_pdf(data, start_page_idx, end_page_idx)
    elif ext in OFFICE_DOC_EXTS:
        return True, office_doc_to_pdf(data, ext)
    elif ext in IMAGE_EXTS:
        return True, image_to_pdf(data)
    else:
        return False, b""


def sanitize_pdf(pdf_bytes, start_page_id=None, end_page_id=None) -> bytes:
    pdf, output_pdf = None, None
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
        num_pages = len(pdf)

        actual_start_page_id = 0 if start_page_id is None else start_page_id
        if actual_start_page_id < 0:  # Check again after setting default
            logger.warning(f"start_page_id ({start_page_id}) is negative, using 0.")
            actual_start_page_id = 0

        actual_end_page_id = end_page_id
        if (
            actual_end_page_id is None
            or actual_end_page_id < 0
            or actual_end_page_id >= num_pages
        ):
            actual_end_page_id = num_pages - 1

        page_indices = []
        if actual_start_page_id > actual_end_page_id:
            logger.warning(
                f"start_page_id ({actual_start_page_id}) is greater than end_page_id ({actual_end_page_id}). Resulting PDF will be empty."
            )
        else:
            page_indices = list(range(actual_start_page_id, actual_end_page_id + 1))

        output_pdf = pdfium.PdfDocument.new()
        if page_indices:
            output_pdf.import_pages(pdf, page_indices)

        output_buffer = io.BytesIO()
        output_pdf.save(output_buffer)
        return output_buffer.getvalue()

    except pdfium.PdfiumError as pe:
        logger.error(f"pypdfium2 error during PDF processing: {pe}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during PDF processing: {e}")
        raise
    finally:
        if pdf is not None:
            pdf.close()
        if output_pdf is not None:
            output_pdf.close()


def get_soffice_cmd() -> str | None:
    return shutil.which("soffice")


def office_doc_to_pdf(content: bytes, ext: str) -> bytes:
    soffice_cmd = get_soffice_cmd()
    if soffice_cmd is None:
        raise RuntimeError("soffice command not found")

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        input_path = output_dir / f"input.{ext}"
        input_path.write_bytes(content)

        target_format = "pdf"

        cmd = [
            soffice_cmd,
            "--headless",
            "--norestore",
            "--convert-to",
            target_format,
            "--outdir",
            str(output_dir),
            str(input_path),
        ]

        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if process.returncode != 0:
            raise RuntimeError(
                f'convert failed, cmd: "{" ".join(cmd)}", output: {process.stdout.decode()}, error: {process.stderr.decode()}'
            )

        output_file = output_dir / f"{input_path.stem}.{target_format}"
        return output_file.read_bytes()


def image_to_pdf(image_bytes: bytes) -> bytes:
    pdf_buffer = io.BytesIO()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image.save(pdf_buffer, format="PDF", save_all=True)
    pdf_bytes = pdf_buffer.getvalue()
    return pdf_bytes


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Adjust title levels in a middle.json file."
    )
    parser.add_argument("pdf_file", type=str, help="Path to the PDF file")
    parser.add_argument(
        "middle_json_path", type=str, help="Path to the middle.json file"
    )
    parser.add_argument("--dump-markdown", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    try:
        with open(args.pdf_file, "rb") as f:
            pdf_bytes = f.read()
        with open(args.middle_json_path, "r", encoding="utf-8") as f:
            middle_json_data = json.load(f)

        for page_num, page_info in enumerate(middle_json_data.get("pdf_info", [])):
            paras_of_layout: list[dict[str, Any]] = page_info.get("para_blocks")
            if not paras_of_layout:
                continue
            for para_block in paras_of_layout:
                para_type = para_block["type"]
                if para_type != BlockType.TITLE:
                    continue
                para_block.pop("level", None)

        adjust_title_level(pdf_bytes, middle_json_data)

        if not args.dump_markdown:
            print(json.dumps(middle_json_data, ensure_ascii=False, indent=2))
        else:
            from mineru.backend.pipeline.pipeline_middle_json_mkcontent import (
                union_make,
            )

            _monkey_patch_mineru()
            markdown = union_make(middle_json_data.get("pdf_info", []), "mm_markdown")
            print(markdown)
    except FileNotFoundError:
        logger.error(f"Error: File not found at {args.middle_json_path}")
    except json.JSONDecodeError:
        logger.error(f"Error: Could not decode JSON from {args.middle_json_path}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
