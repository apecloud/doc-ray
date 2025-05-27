import base64
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from magic_pdf.config.enums import SupportedPdfParseMethod
from magic_pdf.config.ocr_content_type import BlockType
from magic_pdf.data.data_reader_writer import FileBasedDataReader, FileBasedDataWriter
from magic_pdf.data.dataset import PymuDocDataset
from magic_pdf.data.read_api import read_local_office
from magic_pdf.libs import config_reader
from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze

logger = logging.getLogger(__name__)

# TODO: support image formats
SUPPORTED_EXTENSIONS = [
    ".pdf",
    # convert to .pdf first
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
]


def get_soffice_cmd() -> str | None:
    return shutil.which("soffice")


@dataclass
class ParseResult:
    markdown: str
    middle_json: str
    images: dict[str, str] = field(
        default_factory=dict
    )  # image name => base64-decoded image data


class MinerUParser:
    def _detect_device_mode(self) -> str:
        if os.getenv("MINERU_DEVICE_MODE") is not None:
            return os.getenv("MINERU_DEVICE_MODE")
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if torch.mps.is_available():
                return "mps"
        except Exception:
            logger.exception("_detect_device_mode failed")
        return "cpu"

    def _set_config_path(self) -> bool:
        path = Path(os.environ.get("MINERU_CONFIG_JSON", "./magic-pdf.json"))
        if not path.exists():
            return False

        device_mode = self._detect_device_mode()
        if device_mode != "cpu":
            derived_conf = str(path).replace(".json", f"-{device_mode}.json")
            if Path(derived_conf).exists():
                path = Path(derived_conf)

        config_reader.CONFIG_FILE_NAME = str(path.absolute())

        return True

    def parse(self, data: bytes, filename: str) -> ParseResult:
        extension = Path(filename).suffix.lower()
        if extension != ".pdf":
            if get_soffice_cmd() is None:
                raise Exception("soffice command not found")
        if not self._set_config_path():
            raise Exception("magic-pdf.json not found")

        temp_dir = os.environ.get("MINERU_TEMP_FILE_DIR", None)
        temp_dir_obj: tempfile.TemporaryDirectory | None = None
        if not temp_dir:
            temp_dir_obj = tempfile.TemporaryDirectory()
            temp_dir = temp_dir_obj.name

        try:
            doc_path = os.path.join(temp_dir, filename)
            with open(doc_path, "wb") as f:
                f.write(data)

            local_image_dir = os.path.join(temp_dir, "output/images")

            os.makedirs(local_image_dir, exist_ok=True)

            image_writer = FileBasedDataWriter(local_image_dir)

            parse_method = SupportedPdfParseMethod.OCR
            ds: PymuDocDataset = None
            if extension == ".pdf":
                reader1 = FileBasedDataReader("")
                pdf_bytes = reader1.read(doc_path)
                ds = PymuDocDataset(pdf_bytes)
                parse_method = ds.classify()
            else:
                # Note: this requires the "soffice" command to convert office docs into PDF.
                # The "soffice" command is part of LibreOffice, can be installed via:
                #   apt-get install libreoffice
                #   brew install libreoffice
                ds = read_local_office(doc_path)[0]

            from magic_pdf.operators.pipes import PipeResult

            pipe_result: PipeResult = None
            if parse_method == SupportedPdfParseMethod.OCR:
                result = ds.apply(doc_analyze, ocr=True)
                pipe_result = result.pipe_ocr_mode(image_writer)
            else:
                result = ds.apply(doc_analyze, ocr=False)
                pipe_result = result.pipe_txt_mode(image_writer)

            middle_json = None
            if hasattr(pipe_result, "_pipe_res"):
                adjust_title_level(doc_path, pipe_result._pipe_res)
                add_merged_text_field(pipe_result._pipe_res)
                middle_json = json.dumps(pipe_result._pipe_res, ensure_ascii=False)

            markdown = pipe_result.get_markdown("images")
            if middle_json is None:
                middle_json = pipe_result.get_middle_json()

            images_dict = {}
            if os.path.exists(local_image_dir) and os.listdir(local_image_dir):
                for root, _, files in os.walk(local_image_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        with open(file_path, "rb") as f:
                            name = f"images/{file}"
                            images_dict[name] = base64.b64encode(f.read()).decode()

            return ParseResult(
                markdown=markdown, middle_json=middle_json, images=images_dict
            )
        except:
            logger.exception("MinerUParser failed")
            raise
        finally:
            if temp_dir_obj is not None:
                temp_dir_obj.cleanup()


def add_merged_text_field(pipe_res: dict):
    # Lazily import merge_para_with_text here, to avoid initializing
    # ocr_mkcontent.latex_delimiters_config too early. This is because
    # it needs to read the configuration file, and here it can be ensured
    # that the configuration file has been set up.
    from magic_pdf.dict2md.ocr_mkcontent import merge_para_with_text

    def _set_merged_text_field(block: dict[str, Any]):
        block["merged_text"] = merge_para_with_text(block)

    simple_block_types = set([
        BlockType.Text,
        BlockType.List,
        BlockType.Index,
        BlockType.Title,
        BlockType.InterlineEquation,
    ])

    for page_info in pipe_res.get("pdf_info", []):
        paras_of_layout: list[dict[str, Any]] = page_info.get("para_blocks")
        if not paras_of_layout:
            continue
        for para_block in paras_of_layout:
            para_type = para_block["type"]
            if para_type in simple_block_types:
                _set_merged_text_field(para_block)
            elif para_type == BlockType.Image:
                handle_block_types = [BlockType.ImageCaption, BlockType.ImageFootnote]
                for block in para_block["blocks"]:
                    if block["type"] in handle_block_types:
                        _set_merged_text_field(block)
            elif para_type == BlockType.Table:
                handle_block_types = [BlockType.TableCaption, BlockType.TableFootnote]
                for block in para_block["blocks"]:
                    if block["type"] in handle_block_types:
                        _set_merged_text_field(block)


def adjust_title_level(pdf_file: Path | None, pipe_res: dict):
    logger.info("Adjusting title level...")
    raw_text_blocks: dict[int, list[tuple[str, float]]] = {}
    if pdf_file is not None:
        raw_text_blocks = collect_all_text_blocks(pdf_file)
    title_blocks = []
    for page_num, page_info in enumerate(pipe_res.get("pdf_info", [])):
        paras_of_layout: list[dict[str, Any]] = page_info.get("para_blocks")
        if not paras_of_layout:
            continue
        for para_block in paras_of_layout:
            para_type = para_block["type"]
            if para_type != BlockType.Title:
                continue
            has_level = para_block.get("level", None)
            if has_level is not None:
                logger.info(
                    "MinerU has already set a title level; skipping adjustment."
                )
                return

            raw_text_map = {}
            for raw_text in raw_text_blocks.get(page_num, []):
                raw_text_map[raw_text[0]] = raw_text[1]

            font_size = None
            lines = para_block.get("lines", [])
            for line in lines:
                spans = line.get("spans", [])
                for span in spans:
                    content = span.get("content", "").strip()
                    if content in raw_text_map:
                        font_size = raw_text_map[content]

            # If the font size cannot be obtained directly from the PDF document,
            # calculate an approximate font size based on the bounding box height.
            if font_size is None:
                bbox = para_block.get("bbox", None)
                if bbox is not None:
                    height = bbox[3] - bbox[1]
                    lines = para_block.get("lines", None)
                    if lines is not None and len(lines) > 1:
                        height = height / len(lines)
                    # NOTE: This formula is derived from simple observation
                    # and may not be applicable to all situations.
                    font_size = height * 0.78

            if font_size is None:
                continue

            title_blocks.append(
                (
                    font_size,
                    para_block,
                )
            )

    if len(title_blocks) == 0:
        return

    title_blocks.sort(key=lambda x: x[0], reverse=True)
    level = 1
    prev_font_size = None
    delta = 0.2
    max_level = 8
    for font_size, para_block in title_blocks:
        if prev_font_size is not None and prev_font_size - font_size > delta:
            level += 1
            if level > max_level:
                level = max_level
        para_block["level"] = level
        prev_font_size = font_size


def collect_all_text_blocks(pdf_path: Path) -> dict[int, list[tuple[str, float]]]:
    try:
        with fitz.open(pdf_path) as doc:
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
