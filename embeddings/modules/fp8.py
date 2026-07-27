import logging

import torch
import torch.nn as nn

from .gpu import _force_gc

logger = logging.getLogger(__name__)


def _check_te_available():
    try:
        import transformer_engine.pytorch as te  # noqa: F401
        return True
    except ImportError:
        logger.warning("transformer-engine not installed — FP8 disabled")
        return False

def _check_fp8_support():
    if not torch.cuda.is_available():
        return False
    cap = torch.cuda.get_device_capability()
    supported = cap[0] > 8 or (cap[0] == 8 and cap[1] >= 9)
    if supported:
        logger.info(f"GPU sm_{cap[0]}{cap[1]} — FP8 supported")
    else:
        logger.warning(f"GPU sm_{cap[0]}{cap[1]} — FP8 requires >= sm_89")
    return supported

def swap_linear_to_te(module):
    import transformer_engine.pytorch as te
    replacements = []
    for _name, child_module in module.named_modules():
        for child_name, child in child_module.named_children():
            if isinstance(child, nn.Linear):
                if child.in_features % 16 == 0 and child.out_features % 16 == 0:
                    replacements.append((child_module, child_name, child))
    swapped = 0
    for parent, child_name, old_linear in replacements:
        te_linear = te.Linear(
            old_linear.in_features, old_linear.out_features,
            bias=old_linear.bias is not None,
            params_dtype=old_linear.weight.dtype,
            device=old_linear.weight.device,
        )
        te_linear.weight.data.copy_(old_linear.weight.data)
        if old_linear.bias is not None:
            te_linear.bias.data.copy_(old_linear.bias.data)
        setattr(parent, child_name, te_linear)
        swapped += 1
        del old_linear
    _force_gc("cuda")
    logger.info(f"Swapped {swapped} nn.Linear → te.Linear")
    return swapped

def swap_rmsnorm_to_fused(module):
    import transformer_engine.pytorch as te
    swapped = 0
    for name, parent in module.named_modules():
        for child_name, child in parent.named_children():
            if type(child).__name__ == "Qwen3RMSNorm":
                fused = te.RMSNorm(child.weight.shape[0], eps=child.variance_epsilon)
                fused.weight = child.weight
                setattr(parent, child_name, fused)
                swapped += 1
    logger.info(f"Swapped {swapped} Qwen3RMSNorm → te.RMSNorm (fused)")
    return swapped
