"""Convert an existing OpenAI-format Whisper checkpoint (*.pt) to MLX format locally.

This avoids re-downloading ~1.6 GB when the user already has the model in ~/.cache/whisper.
Output layout matches what `mlx_whisper.load_models.load_model` expects:
    <out_dir>/config.json
    <out_dir>/weights.safetensors
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("lazos.whisper_convert")


class ConversionError(RuntimeError):
    pass


def _remap(key: str, value):  # type: ignore[no-untyped-def]
    key = key.replace("mlp.0", "mlp1").replace("mlp.2", "mlp2")
    if "conv" in key and value.ndim == 3:
        # PyTorch Conv1d: (out, in, k) → MLX Conv1d: (out, k, in)
        value = value.swapaxes(1, 2)
    return key, value


def convert_checkpoint(
    checkpoint: Path,
    out_dir: Path,
    *,
    progress: Callable[[float, str], None] | None = None,
) -> None:
    import mlx.core as mx
    import numpy as np
    import torch
    from mlx.utils import tree_flatten
    from mlx_whisper import whisper

    def report(p: float, msg: str) -> None:
        log.info(msg)
        if progress:
            progress(p, msg)

    report(0.05, "loading PyTorch checkpoint")
    ckpt = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    if "dims" not in ckpt or "model_state_dict" not in ckpt:
        raise ConversionError("not an OpenAI Whisper checkpoint")
    dims = dict(ckpt["dims"])

    report(0.4, "converting tensors to MLX float16")
    weights: dict[str, mx.array] = {}
    for key, tensor in ckpt["model_state_dict"].items():
        if key == "encoder.positional_embedding":
            continue  # recomputed (sinusoidal) by the MLX encoder
        key, arr = _remap(key, tensor.to(torch.float32).numpy())
        weights[key] = mx.array(np.ascontiguousarray(arr)).astype(mx.float16)

    report(0.7, "validating against MLX model definition")
    model = whisper.Whisper(whisper.ModelDimensions(**dims), mx.float16)
    expected = {k: v.shape for k, v in tree_flatten(model.parameters())}
    expected.pop("alignment_heads", None)
    missing = set(expected) - set(weights)
    unexpected = set(weights) - set(expected)
    if missing or unexpected:
        raise ConversionError(f"key mismatch: missing={sorted(missing)[:5]} unexpected={sorted(unexpected)[:5]}")
    for k, shape in expected.items():
        if tuple(weights[k].shape) != tuple(shape):
            raise ConversionError(f"shape mismatch for {k}: {weights[k].shape} != {shape}")

    report(0.85, "writing MLX weights")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "weights.tmp.safetensors"
    mx.save_safetensors(str(tmp), weights)
    tmp.rename(out_dir / "weights.safetensors")
    (out_dir / "config.json").write_text(json.dumps({**dims, "model_type": "whisper"}, indent=2))
    report(1.0, "conversion complete")
