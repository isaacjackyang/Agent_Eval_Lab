from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterator


SUPPORTED_RELAYER_MODES = {
    "metadata_only",
    "runtime_patch",
    "mock_layer_stack",
}

RUNNER_RELAYER_SUPPORTED_MODES: dict[str, tuple[str, ...]] = {
    "llama_cpp_agent": ("metadata_only",),
    "openclaw_cli": ("metadata_only",),
    "session_mock": ("metadata_only", "mock_layer_stack"),
    "mock_layer_stack": ("metadata_only", "mock_layer_stack"),
}


@dataclass(frozen=True)
class RelayerConfig:
    start_layer: int
    end_layer: int
    repeat_count: int = 1

    @property
    def block_len(self) -> int:
        return self.end_layer - self.start_layer + 1


@dataclass(frozen=True)
class RelayerPlan:
    execution_order: list[int]


@dataclass(frozen=True)
class RelayerScanSettings:
    num_layers: int
    start_layer_min: int = 0
    end_layer_max: int | None = None
    min_block_len: int = 1
    max_block_len: int | None = None
    repeat_count: int = 1


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "candidate"


def _relayer_section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get("relayer", {})
    return section if isinstance(section, dict) else {}


def relayer_runtime_backend_enabled(config: dict[str, Any]) -> bool:
    section = _relayer_section(config)
    backend = section.get("runtime_backend", {})
    if not isinstance(backend, dict):
        return False
    command = backend.get("command")
    if isinstance(command, list):
        return any(str(item).strip() for item in command)
    return bool(str(command or "").strip())


def relayer_supported_modes_for_runner(runner_name: str | None, *, config: dict[str, Any] | None = None) -> list[str]:
    normalized = str(runner_name or "").strip().lower()
    if not normalized:
        return ["metadata_only"]
    modes = RUNNER_RELAYER_SUPPORTED_MODES.get(normalized)
    if modes is None:
        return ["metadata_only"]
    resolved = list(modes)
    if config and relayer_runtime_backend_enabled(config) and "runtime_patch" not in resolved:
        resolved.append("runtime_patch")
    return resolved


def is_relayer_enabled(config: dict[str, Any]) -> bool:
    return bool(_relayer_section(config).get("enabled", False))


def resolve_relayer_num_layers(config: dict[str, Any]) -> int | None:
    section = _relayer_section(config)
    raw_value = section.get("num_layers")
    if raw_value in (None, ""):
        return None
    num_layers = int(raw_value)
    if num_layers <= 0:
        raise ValueError("relayer.num_layers must be greater than 0.")
    return num_layers


def validate_relayer_config(num_layers: int, relayer_config: RelayerConfig) -> None:
    if num_layers <= 0:
        raise ValueError("num_layers must be greater than 0.")
    if relayer_config.repeat_count < 1:
        raise ValueError("repeat_count must be at least 1.")
    if relayer_config.start_layer < 0:
        raise ValueError("start_layer must be >= 0.")
    if relayer_config.end_layer < relayer_config.start_layer:
        raise ValueError("end_layer must be >= start_layer.")
    if relayer_config.end_layer >= num_layers:
        raise ValueError("end_layer must be < num_layers.")


def build_relayer_plan(num_layers: int, relayer_config: RelayerConfig) -> RelayerPlan:
    validate_relayer_config(num_layers, relayer_config)
    prefix = list(range(0, relayer_config.start_layer))
    block = list(range(relayer_config.start_layer, relayer_config.end_layer + 1))
    suffix = list(range(relayer_config.end_layer + 1, num_layers))
    execution_order = prefix + (block * (relayer_config.repeat_count + 1)) + suffix
    return RelayerPlan(execution_order=execution_order)


def relayer_config_id(model_name: str, relayer_config: RelayerConfig) -> str:
    return (
        f"{_slugify(model_name)}__"
        f"s{relayer_config.start_layer}_e{relayer_config.end_layer}_r{relayer_config.repeat_count}"
    )


def resolve_active_relayer_config(config: dict[str, Any]) -> RelayerConfig | None:
    section = _relayer_section(config)
    if not section.get("enabled", False):
        return None
    if section.get("start_layer") in (None, "") or section.get("end_layer") in (None, ""):
        raise ValueError("relayer.start_layer and relayer.end_layer are required when relayer.enabled=true.")
    return RelayerConfig(
        start_layer=int(section["start_layer"]),
        end_layer=int(section["end_layer"]),
        repeat_count=int(section.get("repeat_count", 1)),
    )


def resolve_active_relayer_plan(config: dict[str, Any]) -> RelayerPlan | None:
    relayer_config = resolve_active_relayer_config(config)
    if relayer_config is None:
        return None
    num_layers = resolve_relayer_num_layers(config)
    if num_layers is None:
        raise ValueError("relayer.num_layers is required when relayer.enabled=true.")
    return build_relayer_plan(num_layers, relayer_config)


def resolve_relayer_scan_settings(config: dict[str, Any]) -> RelayerScanSettings:
    section = _relayer_section(config)
    scan = section.get("scan", {})
    if not isinstance(scan, dict):
        scan = {}

    num_layers = resolve_relayer_num_layers(config)
    if num_layers is None:
        raise ValueError("relayer.num_layers is required to build a relayer scan.")

    end_layer_max = int(scan.get("end_layer_max", num_layers - 1))
    settings = RelayerScanSettings(
        num_layers=num_layers,
        start_layer_min=int(scan.get("start_layer_min", 0)),
        end_layer_max=end_layer_max,
        min_block_len=int(scan.get("min_block_len", 1)),
        max_block_len=int(scan["max_block_len"]) if scan.get("max_block_len") not in (None, "") else None,
        repeat_count=int(scan.get("repeat_count", section.get("repeat_count", 1))),
    )
    if settings.start_layer_min < 0:
        raise ValueError("relayer.scan.start_layer_min must be >= 0.")
    if settings.end_layer_max is None:
        raise ValueError("relayer.scan.end_layer_max could not be resolved.")
    if settings.end_layer_max >= settings.num_layers:
        raise ValueError("relayer.scan.end_layer_max must be < relayer.num_layers.")
    if settings.end_layer_max < settings.start_layer_min:
        raise ValueError("relayer.scan.end_layer_max must be >= start_layer_min.")
    if settings.min_block_len < 1:
        raise ValueError("relayer.scan.min_block_len must be >= 1.")
    if settings.max_block_len is not None and settings.max_block_len < settings.min_block_len:
        raise ValueError("relayer.scan.max_block_len must be >= min_block_len.")
    if settings.repeat_count < 1:
        raise ValueError("relayer.scan.repeat_count must be >= 1.")
    return settings


def generate_relayer_configs(settings: RelayerScanSettings) -> Iterator[RelayerConfig]:
    for start_layer in range(settings.start_layer_min, settings.end_layer_max + 1):
        for end_layer in range(start_layer, settings.end_layer_max + 1):
            config = RelayerConfig(
                start_layer=start_layer,
                end_layer=end_layer,
                repeat_count=settings.repeat_count,
            )
            block_len = config.block_len
            if block_len < settings.min_block_len:
                continue
            if settings.max_block_len is not None and block_len > settings.max_block_len:
                continue
            validate_relayer_config(settings.num_layers, config)
            yield config


def relayer_scan_candidate_count(config: dict[str, Any]) -> int:
    settings = resolve_relayer_scan_settings(config)
    return sum(1 for _ in generate_relayer_configs(settings))


def summarize_relayer_config(config: dict[str, Any]) -> dict[str, Any]:
    section = _relayer_section(config)
    runner_name = str(config.get("runner") or "").strip().lower() or None
    mode = str(section.get("mode", "metadata_only")).strip().lower() or "metadata_only"
    supported_modes = relayer_supported_modes_for_runner(runner_name, config=config)

    num_layers: int | None = None
    num_layers_error: str | None = None
    try:
        num_layers = resolve_relayer_num_layers(config)
    except Exception as exc:
        num_layers_error = str(exc)

    active_config: RelayerConfig | None = None
    active_config_error: str | None = None
    try:
        active_config = resolve_active_relayer_config(config)
    except Exception as exc:
        active_config_error = str(exc)

    scan_settings: RelayerScanSettings | None = None
    scan_candidate_total: int | None = None
    scan_error: str | None = None
    try:
        scan_settings = resolve_relayer_scan_settings(config)
        scan_candidate_total = relayer_scan_candidate_count(config)
    except Exception as exc:
        scan_error = str(exc)

    return {
        "configured": bool(section),
        "enabled": bool(section.get("enabled", False)),
        "mode": mode,
        "runner": runner_name,
        "runtime_supported_modes": supported_modes,
        "runtime_patch_supported": "runtime_patch" in supported_modes,
        "num_layers": num_layers,
        "num_layers_error": num_layers_error,
        "active_config": asdict(active_config) if active_config is not None else None,
        "active_config_error": active_config_error,
        "scan_supported": scan_error is None,
        "scan_candidate_count": scan_candidate_total,
        "scan_error": scan_error,
        "scan_backend": "mock_layer_stack",
        "scan_note": "Synthetic relayer scan currently runs on mock_layer_stack, not a real model forward path.",
        "external_runtime_bridge": relayer_runtime_backend_enabled(config),
        "scan_settings": (
            {
                "num_layers": scan_settings.num_layers,
                "start_layer_min": scan_settings.start_layer_min,
                "end_layer_max": scan_settings.end_layer_max,
                "min_block_len": scan_settings.min_block_len,
                "max_block_len": scan_settings.max_block_len,
                "repeat_count": scan_settings.repeat_count,
            }
            if scan_settings is not None
            else None
        ),
    }


def apply_relayer_config(base_config: dict[str, Any], relayer_config: RelayerConfig) -> dict[str, Any]:
    config = copy.deepcopy(base_config)
    section = _relayer_section(config)
    num_layers = resolve_relayer_num_layers(config)
    if num_layers is None:
        raise ValueError("relayer.num_layers is required to apply a relayer config.")
    plan = build_relayer_plan(num_layers, relayer_config)
    section.update(
        {
            "enabled": True,
            "start_layer": relayer_config.start_layer,
            "end_layer": relayer_config.end_layer,
            "repeat_count": relayer_config.repeat_count,
        }
    )
    config["relayer"] = section
    config["relayer_plan"] = {
        "num_layers": num_layers,
        "block_len": relayer_config.block_len,
        **asdict(plan),
    }
    return config


def build_relayer_scan_candidates(base_config: dict[str, Any]) -> list[dict[str, Any]]:
    settings = resolve_relayer_scan_settings(base_config)
    model_name = (
        str(base_config.get("llama_cpp", {}).get("model"))
        or str(base_config.get("openclaw", {}).get("model"))
        or str(base_config.get("config_id", "model"))
    )
    candidates: list[dict[str, Any]] = []
    for relayer_config in generate_relayer_configs(settings):
        candidate = apply_relayer_config(base_config, relayer_config)
        candidate["config_id"] = relayer_config_id(model_name, relayer_config)
        candidate["mutation_profile"] = candidate["config_id"]
        candidate["mutation_strategy"] = "relayer_scan"
        candidate["mutation_target"] = "relayer.start_layer x relayer.end_layer"
        candidate["mutation_before"] = None
        candidate["mutation_after"] = {
            "start_layer": relayer_config.start_layer,
            "end_layer": relayer_config.end_layer,
            "repeat_count": relayer_config.repeat_count,
        }
        candidate["mutation_notes"] = (
            f"Relayer scan cell start_layer={relayer_config.start_layer}, "
            f"end_layer={relayer_config.end_layer}, repeat_count={relayer_config.repeat_count}."
        )
        candidate["heat_map_coordinates"] = {
            "x_axis": "relayer.start_layer",
            "x_label": "Start Layer",
            "x_value": relayer_config.start_layer,
            "y_axis": "relayer.end_layer",
            "y_label": "End Layer",
            "y_value": relayer_config.end_layer,
        }
        candidates.append(candidate)
    return candidates


def resolve_relayer_runtime_context(
    config: dict[str, Any],
    *,
    runtime_patch_supported: bool,
    runtime_label: str,
    supported_modes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    section = _relayer_section(config)
    resolved_supported_modes = list(supported_modes or ["metadata_only"])
    if "metadata_only" not in resolved_supported_modes:
        resolved_supported_modes.insert(0, "metadata_only")
    if runtime_patch_supported and "runtime_patch" not in resolved_supported_modes:
        resolved_supported_modes.append("runtime_patch")
    if not section.get("enabled", False):
        return {
            "enabled": False,
            "applied": False,
            "mode": "disabled",
            "message": "Relayer disabled.",
            "config": None,
            "plan": None,
            "num_layers": resolve_relayer_num_layers(config),
            "runtime_supported_modes": resolved_supported_modes,
        }

    mode = str(section.get("mode", "metadata_only")).strip().lower() or "metadata_only"
    if mode not in SUPPORTED_RELAYER_MODES:
        raise ValueError(f"Unsupported relayer mode: {mode}")

    relayer_config = resolve_active_relayer_config(config)
    num_layers = resolve_relayer_num_layers(config)
    if relayer_config is None or num_layers is None:
        raise ValueError("Active relayer configuration is incomplete.")
    plan = build_relayer_plan(num_layers, relayer_config)

    applied = False
    if mode == "metadata_only":
        message = (
            f"Relayer plan prepared for {runtime_label}, but mode=metadata_only so no runtime layer patch "
            f"was applied. execution_order length={len(plan.execution_order)}."
        )
    elif mode in resolved_supported_modes:
        applied = True
        if mode == "runtime_patch":
            message = (
                f"Relayer runtime patch active on {runtime_label}: "
                f"layers {relayer_config.start_layer}-{relayer_config.end_layer} "
                f"repeat_count={relayer_config.repeat_count}."
            )
        else:
            message = (
                f"Relayer runtime backend {mode} active on {runtime_label}: "
                f"layers {relayer_config.start_layer}-{relayer_config.end_layer} "
                f"repeat_count={relayer_config.repeat_count}."
            )
    else:
        supported_text = ", ".join(resolved_supported_modes)
        raise RuntimeError(
            f"Relayer mode={mode} requires a runtime backend, but {runtime_label} supports only {supported_text}."
        )

    return {
        "enabled": True,
        "applied": applied,
        "mode": mode,
        "message": message,
        "range_text": f"{relayer_config.start_layer}-{relayer_config.end_layer}",
        "config": asdict(relayer_config),
        "plan": asdict(plan),
        "num_layers": num_layers,
        "runtime_supported_modes": resolved_supported_modes,
    }
