"""Dedicated Modal app for the ModernBERT fine-tune — ``modernbert-train``.

Deployed once; ``modal run modernbert/modal_app.py --epochs 5`` spawns an L4
GPU container that:

- bundles this repo's ``src/`` (mailroom_eda) + ``modernbert/`` source,
- pulls the published ``Lucius-Morningstar/mailroom-modernbert-training``
  dataset from the Hub (never a local copy — the pinned revision is the
  contract),
- runs ``modernbert/train.py`` (hierarchical heads, class-weighted loss,
  temperature scaling, held-out test gate),
- persists the checkpoint to the ``modernbert-checkpoints`` Volume AND pushes
  it to the Hub when ``--push-to-hub`` is set.

Deploy:
    HF_TOKEN=... modal deploy modernbert/modal_app.py

Run:
    modal run modernbert/modal_app.py --epochs 5 --push-to-hub Lucius-Morningstar/mailroom-modernbert-classifier
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

APP_NAME = "modernbert-train"
CHECKPOINT_VOLUME_NAME = "modernbert-checkpoints"
HF_VOLUME_NAME = "modernbert-hf-cache"
CHECKPOINT_MOUNT = "/checkpoints"
HF_MOUNT = "/root/.cache/huggingface"

# Repo root (parent of modernbert/) — bundles src/ + modernbert/ at deploy.
ROOT = Path(__file__).resolve().parent.parent

_DEPLOY_ENV_KEYS = ("HF_TOKEN",)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_dir(ROOT / "src", remote_path="/root/src")
    .add_local_dir(ROOT / "modernbert", remote_path="/root/modernbert")
    .uv_pip_install(
        # EDA base — mailroom_eda/__init__ pulls the full stack at import.
        "pandas>=2.3",
        "pyarrow>=25.0",
        "numpy>=2.3",
        "scipy>=1.18",
        "matplotlib>=3.10",
        "seaborn>=0.13",
        "plotly>=7.0",
        "squarify>=0.4",
        "huggingface_hub>=1.29",
        "tiktoken>=0.14",
        "statsmodels>=0.15",
        "scikit-learn>=1.5",
        "wordcloud>=1.9",
        "datasketch>=2.0",
        "polars>=1.0",
        "datasets>=5.0",
        "pyyaml>=6.0",
        "tqdm>=4.0",
        # training stack
        "torch>=2.4",
        "transformers>=4.48",
        "accelerate>=1.0",
        "evaluate>=0.4",
    )
    .env(
        {
            "PYTHONPATH": "/root/src:/root/modernbert",
            "MODERNBERT_ROOT": "/root",
        }
    )
)

checkpoint_vol = modal.Volume.from_name(CHECKPOINT_VOLUME_NAME, create_if_missing=True)
hf_cache = modal.Volume.from_name(HF_VOLUME_NAME, create_if_missing=True)

app = modal.App(
    APP_NAME,
    image=image,
    tags={
        "project": "digital-mailroom",
        "package": "mailroom-corpus-eda",
        "purpose": "modernbert-train",
    },
)


def _config_secrets() -> list[modal.Secret]:
    values = {k: os.environ.get(k) for k in _DEPLOY_ENV_KEYS if os.environ.get(k)}
    if not values:
        return []
    return [modal.Secret.from_dict(values)]


@app.function(
    # Modal >=1.0 configures GPUs by string ("L4", "A10G", "H100:2") — the
    # old modal.gpu.L4() object API was removed in the 1.x SDK line.
    gpu="L4",
    volumes={CHECKPOINT_MOUNT: checkpoint_vol, HF_MOUNT: hf_cache},
    secrets=_config_secrets(),
    timeout=60 * 60 * 4,
    startup_timeout=60 * 10,
)
def train(epochs: int = 5, batch_size: int = 16, grad_accum: int = 2,
          lr: float = 2e-5, push_to_hub: str = "", eval_test: bool = True) -> dict:
    """Run the fine-tune inside the GPU container."""
    import subprocess
    import sys

    cmd = [
        sys.executable, "/root/modernbert/train.py",
        "--data", "Lucius-Morningstar/mailroom-modernbert-training",
        "--output", f"{CHECKPOINT_MOUNT}/latest",
        "--epochs", str(epochs),
        "--batch-size", str(batch_size),
        "--grad-accum", str(grad_accum),
        "--lr", str(lr),
    ]
    if push_to_hub:
        cmd += ["--push-to-hub", push_to_hub]
    if eval_test:
        cmd += ["--eval-test"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr[-4000:])
        raise RuntimeError(f"train.py exited {result.returncode}")
    checkpoint_vol.commit()
    return {"returncode": result.returncode, "epochs": epochs,
            "push_to_hub": push_to_hub}


@app.local_entrypoint()
def main(epochs: int = 5, batch_size: int = 16, grad_accum: int = 2,
         lr: float = 2e-5, push_to_hub: str = "", eval_test: bool = True) -> None:
    print(f"modernbert-train: epochs={epochs} batch={batch_size} "
          f"grad_accum={grad_accum} lr={lr} push={push_to_hub or 'no'}")
    train.remote(epochs=epochs, batch_size=batch_size, grad_accum=grad_accum,
                 lr=lr, push_to_hub=push_to_hub, eval_test=eval_test)