# From: https://raw.githubusercontent.com/opendatalab/MinerU/master/scripts/download_models_hf.py

import copy
import json
import os
from urllib.request import urlopen

use_modelscope = os.getenv("USE_MODELSCOPE", None) == "1"

if use_modelscope:
    from modelscope import snapshot_download
else:
    from huggingface_hub import snapshot_download


def download_json(url):
    with urlopen(url) as response:
        data = json.loads(response.read().decode())
    return data


def download_and_modify_json(url, local_filename, modifications):
    if os.path.exists(local_filename):
        data = json.load(open(local_filename))
        config_version = data.get('config_version', '0.0.0')
        if config_version < '1.2.0':
            data = download_json(url)
    else:
        data = download_json(url)

    for key, value in modifications.items():
        data[key] = value

    with open(local_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _save_json_config(data, filepath):
    """Helper function to save dictionary data to a JSON file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f'Successfully generated derived configuration file: {filepath}')


def prepare():
    cache_dir = os.environ.get("CACHE_DIR", None)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    mineru_patterns = [
        # "models/Layout/LayoutLMv3/*",
        "models/Layout/YOLO/*",
        "models/MFD/YOLO/*",
        "models/MFR/unimernet_hf_small_2503/*",
        "models/OCR/paddleocr_torch/*",
        # "models/TabRec/TableMaster/*",
        # "models/TabRec/StructEqTable/*",
    ]
    model_dir = snapshot_download('opendatalab/PDF-Extract-Kit-1.0', allow_patterns=mineru_patterns, cache_dir=cache_dir)

    if use_modelscope:
        layoutreader_model_dir = snapshot_download('ppaanngggg/layoutreader')
    else:
        layoutreader_pattern = [
            "*.json",
            "*.safetensors",
        ]
        layoutreader_model_dir = snapshot_download('hantian/layoutreader', allow_patterns=layoutreader_pattern, cache_dir=cache_dir)

    model_dir = model_dir + '/models'

    print(f'model_dir is: {model_dir}')
    print(f'layoutreader_model_dir is: {layoutreader_model_dir}')

    json_url = 'https://github.com/opendatalab/MinerU/raw/master/magic-pdf.template.json'
    if use_modelscope:
        json_url = 'https://gcore.jsdelivr.net/gh/opendatalab/MinerU@master/magic-pdf.template.json'
    config_file_name = 'magic-pdf.json'
    config_file = os.path.join(".", config_file_name)

    json_mods = {
        'models-dir': model_dir,
        'layoutreader-model-dir': layoutreader_model_dir,
    }

    download_and_modify_json(json_url, config_file, json_mods)
    print(f'The configuration file has been configured successfully, the path is: {config_file}')

    # Generate derived configuration files (cuda and mps)
    print('\nGenerating derived configurations...')

    # Load the freshly created/updated base configuration
    with open(config_file, 'r', encoding='utf-8') as f:
        base_config_data = json.load(f)

    base_filename = os.path.basename(config_file) # e.g., "magic-pdf.json"
    output_dir = os.path.dirname(config_file)     # e.g., "."

    # --- Generate CUDA specific config ---
    # As per request, using "cude" for the device-mode value.
    cuda_config_data = copy.deepcopy(base_config_data)
    cuda_config_data['device-mode'] = 'cude'
    cuda_filename = base_filename.replace('.json', '-cuda.json')
    cuda_filepath = os.path.join(output_dir, cuda_filename)
    _save_json_config(cuda_config_data, cuda_filepath)

    # --- Generate MPS specific config ---
    mps_config_data = copy.deepcopy(base_config_data)
    mps_config_data['device-mode'] = 'mps'
    mps_filename = base_filename.replace('.json', '-mps.json')
    mps_filepath = os.path.join(output_dir, mps_filename)
    _save_json_config(mps_config_data, mps_filepath)


if __name__ == '__main__':
    prepare()
