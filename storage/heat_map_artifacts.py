from __future__ import annotations

import csv
import math
import struct
import zlib
from pathlib import Path
from typing import Any

from storage.history_writer import write_json

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_AVAILABLE = True
    try:
        _BICUBIC = Image.Resampling.BICUBIC
    except AttributeError:  # pragma: no cover - Pillow < 9
        _BICUBIC = Image.BICUBIC
except ImportError:  # pragma: no cover - fallback path
    Image = None
    ImageDraw = None
    ImageFont = None
    _PIL_AVAILABLE = False
    _BICUBIC = None


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_FIG_BG = (247, 245, 241, 255)
_PANEL_BG = (255, 252, 248, 255)
_TEXT_PRIMARY = (39, 43, 51, 255)
_TEXT_MUTED = (94, 104, 118, 255)
_BORDER = (214, 206, 196, 255)
_INVALID_FILL = (231, 232, 235, 255)
_NEUTRAL_FILL = (244, 240, 235, 255)
_POSITIVE = (179, 27, 61, 255)
_NEGATIVE = (43, 108, 176, 255)
_BASELINE_MARK = (28, 33, 40, 255)
_BEST_MARK = (28, 206, 79, 255)
_WHITE = (255, 255, 255, 255)


def _channel_projection(summary: dict[str, Any], channel_id: str) -> dict[str, Any]:
    channels = summary.get("channels", {})
    if not isinstance(channels, dict) or channel_id not in channels:
        return summary

    channel = channels[channel_id]
    top_candidates = []
    for item in summary.get("top_candidates", []):
        if channel_id == "combined":
            score = item.get("combined", {}).get("suite_score", item.get("suite_score_c"))
            delta = item.get("combined", {}).get("delta_suite_score", item.get("delta_suite_score"))
        else:
            score = item.get(channel_id, {}).get("suite_score")
            delta = item.get(channel_id, {}).get("delta_suite_score")
        top_candidates.append(
            {
                **item,
                "suite_score_c": score,
                "delta_suite_score": delta,
            }
        )

    projected_matrix: list[dict[str, Any]] = []
    for row in channel.get("matrix", []):
        cells: list[dict[str, Any]] = []
        for cell in row.get("cells", []):
            if cell.get("status") == "not_run":
                cells.append(dict(cell))
                continue
            cells.append(
                {
                    **cell,
                    "suite_score_c": cell.get("suite_score"),
                }
            )
        projected_matrix.append({"y_value": row.get("y_value"), "cells": cells})

    best_cell = channel.get("best_cell")
    projected_best = None
    if isinstance(best_cell, dict):
        projected_best = {
            **best_cell,
            "suite_score_c": best_cell.get("suite_score"),
        }

    return {
        **summary,
        "matrix": projected_matrix,
        "top_candidates": top_candidates,
        "best_cell": projected_best,
        "channel_id": channel_id,
        "channel_label": channel.get("label", channel_id),
    }


def _format_metric(value: Any, digits: int = 4) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _append_bilingual(lines: list[str], english: str, chinese: str, *, bullet: bool = False) -> None:
    if bullet:
        lines.append(f"- {english}")
        lines.append(f"  {chinese}")
        return
    lines.append(english)
    lines.append(chinese)


def _iter_cells(summary: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row in summary.get("matrix", []):
        for cell in row.get("cells", []):
            if not isinstance(cell, dict):
                continue
            normalized = dict(cell)
            normalized.setdefault("status", "ok" if "delta_suite_score" in normalized else "not_run")
            cells.append(normalized)
    return cells


def _csv_value(cell: dict[str, Any], key: str) -> Any:
    if cell.get("status") == "not_run":
        return ""
    return cell.get(key, "")


def _write_matrix_csv(path: Path, summary: dict[str, Any]) -> None:
    x_values = summary.get("x_values", [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([summary.get("y_axis", "y_axis")] + [str(value) for value in x_values])
        for row in summary.get("matrix", []):
            writer.writerow(
                [str(row.get("y_value"))]
                + [str(_csv_value(cell, "delta_suite_score")) for cell in row.get("cells", [])]
            )


def _write_cells_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = [
        "config_id",
        "x_axis",
        "x_label",
        "x_value",
        "y_axis",
        "y_label",
        "y_value",
        "suite_score_c",
        "fitness",
        "regression_pass_rate",
        "honesty_score",
        "delta_suite_score",
        "delta_fitness",
        "improved_vs_baseline",
        "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in _iter_cells(summary):
            writer.writerow({field: cell.get(field, "") for field in fields})


def _write_top_candidates_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = [
        "config_id",
        "x_value",
        "y_value",
        "suite_score_c",
        "fitness",
        "regression_pass_rate",
        "honesty_score",
        "delta_suite_score",
        "delta_fitness",
        "improved_vs_baseline",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in summary.get("top_candidates", []):
            writer.writerow({field: cell.get(field, "") for field in fields})


def _set_pixel(buffer: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    offset = (y * width + x) * 4
    buffer[offset : offset + 4] = bytes(color)


def _fill_rect(
    buffer: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int, int],
) -> None:
    for y in range(y0, y0 + rect_height):
        for x in range(x0, x0 + rect_width):
            _set_pixel(buffer, width, height, x, y, color)


def _stroke_rect(
    buffer: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int, int],
    thickness: int = 1,
) -> None:
    for index in range(thickness):
        for x in range(x0 + index, x0 + rect_width - index):
            _set_pixel(buffer, width, height, x, y0 + index, color)
            _set_pixel(buffer, width, height, x, y0 + rect_height - 1 - index, color)
        for y in range(y0 + index, y0 + rect_height - index):
            _set_pixel(buffer, width, height, x0 + index, y, color)
            _set_pixel(buffer, width, height, x0 + rect_width - 1 - index, y, color)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _encode_png(
    *,
    width: int,
    height: int,
    rgba: bytearray,
    metadata: dict[str, str],
) -> bytes:
    rows = bytearray()
    for y in range(height):
        start = y * width * 4
        end = start + (width * 4)
        rows.append(0)
        rows.extend(rgba[start:end])

    chunks = [
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
    ]
    for key, value in metadata.items():
        chunks.append(_png_chunk(b"tEXt", f"{key}\x00{value}".encode("latin-1", errors="replace")))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9)))
    chunks.append(_png_chunk(b"IEND", b""))
    return PNG_SIGNATURE + b"".join(chunks)


def _lerp_channel(start: int, end: int, t: float) -> int:
    return int(round(start + ((end - start) * max(0.0, min(1.0, t)))))


def _blend(start: tuple[int, int, int, int], end: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return (
        _lerp_channel(start[0], end[0], t),
        _lerp_channel(start[1], end[1], t),
        _lerp_channel(start[2], end[2], t),
        _lerp_channel(start[3], end[3], t),
    )


def _delta_color(delta: float | None, max_abs_delta: float) -> tuple[int, int, int, int]:
    if delta is None or math.isnan(delta):
        return _INVALID_FILL
    if max_abs_delta <= 1e-12:
        return _NEUTRAL_FILL

    magnitude = min(abs(delta) / max_abs_delta, 1.0)
    if delta >= 0:
        return _blend(_NEUTRAL_FILL, _POSITIVE, magnitude)
    return _blend(_NEUTRAL_FILL, _NEGATIVE, magnitude)


def _write_heatmap_png_fallback(path: Path, summary: dict[str, Any], title: str) -> None:
    x_values = summary.get("x_values", [])
    y_values = summary.get("y_values", [])
    grid_width = max(1, len(x_values))
    grid_height = max(1, len(y_values))
    cell_size = 36
    padding = 18
    legend_width = 18
    width = padding * 3 + (grid_width * cell_size) + legend_width + 12
    height = padding * 2 + (grid_height * cell_size)
    rgba = bytearray(list(_FIG_BG) * width * height)

    cells = _iter_cells(summary)
    deltas = [
        float(cell["delta_suite_score"])
        for cell in cells
        if cell.get("status") != "not_run" and cell.get("delta_suite_score") is not None
    ]
    max_abs_delta = max((abs(value) for value in deltas), default=0.0)

    baseline = summary.get("baseline", {})
    best_cell = summary.get("best_cell", {})
    grid_x = padding
    grid_y = padding

    for row_index, row in enumerate(summary.get("matrix", [])):
        for col_index, cell in enumerate(row.get("cells", [])):
            x0 = grid_x + (col_index * cell_size)
            y0 = grid_y + (row_index * cell_size)
            delta = cell.get("delta_suite_score")
            if delta is not None:
                delta = float(delta)
            color = _delta_color(delta, max_abs_delta)
            _fill_rect(rgba, width, height, x0, y0, cell_size - 2, cell_size - 2, color)
            _stroke_rect(rgba, width, height, x0, y0, cell_size - 1, cell_size - 1, _WHITE)
            if (
                cell.get("x_value") == baseline.get("x_value")
                and cell.get("y_value") == baseline.get("y_value")
            ):
                _stroke_rect(rgba, width, height, x0 + 1, y0 + 1, cell_size - 4, cell_size - 4, _BASELINE_MARK, 2)
            if cell.get("config_id") and cell.get("config_id") == best_cell.get("config_id"):
                _stroke_rect(rgba, width, height, x0 + 4, y0 + 4, cell_size - 10, cell_size - 10, _BEST_MARK, 2)

    legend_x = grid_x + (grid_width * cell_size) + padding
    legend_y = grid_y
    legend_height = grid_height * cell_size
    for offset in range(legend_height):
        ratio = 1.0 - (offset / max(1, legend_height - 1))
        delta = ((ratio * 2.0) - 1.0) * max_abs_delta
        color = _delta_color(delta, max_abs_delta)
        _fill_rect(rgba, width, height, legend_x, legend_y + offset, legend_width, 1, color)
    _stroke_rect(rgba, width, height, legend_x, legend_y, legend_width, legend_height, _WHITE)

    metadata = {
        "Title": title,
        "Description": (
            f"suite_id={summary.get('suite_id', '-')}; "
            f"axes={summary.get('y_label', summary.get('y_axis', '-'))} x "
            f"{summary.get('x_label', summary.get('x_axis', '-'))}"
        ),
        "Software": "Agent Eval Lab heat-map builder",
    }
    path.write_bytes(_encode_png(width=width, height=height, rgba=rgba, metadata=metadata))


def _load_font(size: int, *, bold: bool = False) -> Any:
    if not _PIL_AVAILABLE:  # pragma: no cover - protected by caller
        raise RuntimeError("Pillow is not available.")

    font_candidates = []
    if bold:
        font_candidates.extend(
            [
                Path("C:/Windows/Fonts/msjhbd.ttc"),
                Path("C:/Windows/Fonts/msyhbd.ttc"),
                Path("C:/Windows/Fonts/segoeuib.ttf"),
                Path("C:/Windows/Fonts/arialbd.ttf"),
                Path("C:/Windows/Fonts/calibrib.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ]
        )
    else:
        font_candidates.extend(
            [
                Path("C:/Windows/Fonts/msjh.ttc"),
                Path("C:/Windows/Fonts/msyh.ttc"),
                Path("C:/Windows/Fonts/segoeui.ttf"),
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("C:/Windows/Fonts/calibri.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ]
        )

    for candidate in font_candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)

    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _text_bbox(draw: Any, text: str, font: Any) -> tuple[int, int, int, int]:
    return draw.multiline_textbbox((0, 0), text, font=font, spacing=4)


def _text_size(draw: Any, text: str, font: Any) -> tuple[int, int]:
    bbox = _text_bbox(draw, text, font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_centered_text(draw: Any, center_x: float, y: float, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
    width, _ = _text_size(draw, text, font)
    draw.text((center_x - (width / 2), y), text, font=font, fill=fill)


def _draw_rotated_text(
    image: Any,
    *,
    x: int,
    y: int,
    text: str,
    font: Any,
    fill: tuple[int, int, int, int],
    angle: int,
) -> None:
    if not text:
        return
    scratch = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    scratch_draw = ImageDraw.Draw(scratch)
    bbox = _text_bbox(scratch_draw, text, font)
    width = max(1, bbox[2] - bbox[0] + 8)
    height = max(1, bbox[3] - bbox[1] + 8)
    text_image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_image)
    text_draw.multiline_text((4, 4), text, font=font, fill=fill, spacing=4)
    rotated = text_image.rotate(angle, expand=True, resample=_BICUBIC)
    image.alpha_composite(rotated, (x, y))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered)) - 1)))
    return ordered[position]


def _clipped_limit(summary: dict[str, Any]) -> float:
    deltas = [
        abs(float(cell["delta_suite_score"]))
        for cell in _iter_cells(summary)
        if cell.get("status") != "not_run" and cell.get("delta_suite_score") is not None
    ]
    if not deltas:
        return 1.0
    limit = _quantile(deltas, 0.95)
    limit = max(limit, max(deltas) * 0.35)
    return max(limit, 1e-6)


def _tick_step(length: int, max_labels: int = 10) -> int:
    if length <= max_labels:
        return 1
    return max(1, math.ceil(length / max_labels))


def _metric_line(prefix: str, value: Any, digits: int = 4, signed: bool = False) -> str:
    if value is None or value == "":
        return f"{prefix}-"
    if isinstance(value, float):
        if signed:
            return f"{prefix}{value:+.{digits}f}"
        return f"{prefix}{value:.{digits}f}"
    return f"{prefix}{value}"


def _channel_baseline_score(summary: dict[str, Any]) -> Any:
    baseline = summary.get("baseline", {})
    if not isinstance(baseline, dict):
        return None
    channel_id = summary.get("channel_id", "combined")
    if channel_id == "combined":
        combined = baseline.get("combined", {})
        if isinstance(combined, dict):
            return combined.get("suite_score", baseline.get("suite_score_c"))
        return baseline.get("suite_score_c")
    nested = baseline.get(channel_id, {})
    if isinstance(nested, dict):
        return nested.get("suite_score", baseline.get("suite_score_c"))
    return baseline.get("suite_score_c")


def _channel_meta(summary: dict[str, Any]) -> dict[str, Any]:
    channel_id = summary.get("channel_id", "combined")
    if channel_id == "probe_a":
        meta = summary.get("probe_a", {})
    elif channel_id == "probe_b":
        meta = summary.get("probe_b", {})
    else:
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _best_label(summary: dict[str, Any]) -> str:
    best_cell = summary.get("best_cell", {})
    if not isinstance(best_cell, dict) or not best_cell:
        return "Best: not available"
    coord = f"({best_cell.get('start_layer', best_cell.get('y_value', '-'))}, {best_cell.get('end_layer', best_cell.get('x_value', '-'))})"
    score = _format_metric(best_cell.get("suite_score_c"))
    delta = best_cell.get("delta_suite_score")
    delta_part = f", Δ{delta:+.4f}" if isinstance(delta, float) else ""
    return f"Best {coord}: {score}{delta_part}"


def _baseline_label(summary: dict[str, Any]) -> str:
    baseline = summary.get("baseline", {})
    if not isinstance(baseline, dict):
        return "Baseline: not available"
    score = _channel_baseline_score(summary)
    if baseline.get("x_value") is not None and baseline.get("y_value") is not None:
        coord = f"({baseline.get('start_layer', baseline.get('y_value', '-'))}, {baseline.get('end_layer', baseline.get('x_value', '-'))})"
        return f"Baseline {coord}: {_format_metric(score)}"
    return f"Baseline (no duplication): {_format_metric(score)}"


def _cell_center(left: int, top: int, cell_size: int, col_index: int, row_index: int) -> tuple[int, int]:
    return left + int((col_index * cell_size) + (cell_size / 2)), top + int((row_index * cell_size) + (cell_size / 2))


def _marker_index(summary: dict[str, Any], marker: dict[str, Any]) -> tuple[int | None, int | None]:
    x_value = marker.get("x_value")
    y_value = marker.get("y_value")
    if x_value is None or y_value is None:
        return None, None
    x_values = list(summary.get("x_values", []))
    y_values = list(summary.get("y_values", []))
    try:
        return x_values.index(x_value), y_values.index(y_value)
    except ValueError:
        return None, None


def _draw_marker(draw: Any, *, cx: int, cy: int, radius: int, outline: tuple[int, int, int, int], width: int, fill: tuple[int, int, int, int] | None = None) -> None:
    bounds = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.ellipse(bounds, outline=outline, width=width, fill=fill)


def _panel_title(summary: dict[str, Any]) -> str:
    label = str(summary.get("channel_label") or "Combined Delta")
    return f"{label} delta (clipped)"


def _panel_subtitle(summary: dict[str, Any], suite_id: str) -> str:
    meta = _channel_meta(summary)
    task_type = meta.get("task_type")
    if task_type:
        return f"{suite_id} {task_type} - Difference from Baseline (Clipped)"
    return f"{suite_id} - Difference from Baseline (Clipped)"


def _draw_legend_box(
    draw: Any,
    *,
    box: tuple[int, int, int, int],
    summary: dict[str, Any],
    clip_limit: float,
    small_font: Any,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=16, fill=(255, 255, 255, 220), outline=_BORDER, width=2)
    baseline_text = _baseline_label(summary)
    best_text = _best_label(summary)
    clip_text = f"Clipped at ±{clip_limit:.4f}"

    icon_y = top + 20
    _draw_marker(draw, cx=left + 18, cy=icon_y + 2, radius=7, outline=_BASELINE_MARK, width=2)
    draw.text((left + 34, icon_y - 8), baseline_text, font=small_font, fill=_TEXT_PRIMARY)

    icon_y += 28
    _draw_marker(draw, cx=left + 18, cy=icon_y + 2, radius=7, outline=_BEST_MARK, width=3)
    draw.text((left + 34, icon_y - 8), best_text, font=small_font, fill=_TEXT_PRIMARY)

    draw.text((left + 16, bottom - 28), clip_text, font=small_font, fill=_TEXT_MUTED)


def _draw_colorbar(
    draw: Any,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    limit: float,
    small_font: Any,
) -> None:
    for offset in range(height):
        ratio = 1.0 - (offset / max(1, height - 1))
        delta = ((ratio * 2.0) - 1.0) * limit
        color = _delta_color(delta, limit)
        draw.rectangle((x, y + offset, x + width, y + offset), fill=color)

    draw.rectangle((x, y, x + width, y + height), outline=_BORDER, width=1)
    draw.text((x + width + 10, y - 8), f"+{limit:.4f}", font=small_font, fill=_TEXT_MUTED)
    draw.text((x + width + 10, y + (height / 2) - 8), "0.0000", font=small_font, fill=_TEXT_MUTED)
    draw.text((x + width + 10, y + height - 18), f"-{limit:.4f}", font=small_font, fill=_TEXT_MUTED)
    draw.text((x - 8, y + height + 12), "Score delta vs baseline", font=small_font, fill=_TEXT_MUTED)


def _draw_axis_ticks(
    image: Any,
    draw: Any,
    *,
    plot_left: int,
    plot_top: int,
    plot_width: int,
    plot_height: int,
    cell_size: int,
    summary: dict[str, Any],
    axis_font: Any,
) -> None:
    x_values = list(summary.get("x_values", []))
    y_values = list(summary.get("y_values", []))

    x_step = _tick_step(len(x_values))
    for idx, value in enumerate(x_values):
        if idx % x_step != 0 and idx != len(x_values) - 1:
            continue
        cx, _ = _cell_center(plot_left, plot_top, cell_size, idx, 0)
        label = str(value)
        label_width, label_height = _text_size(draw, label, axis_font)
        draw.text((cx - (label_width / 2), plot_top + plot_height + 14), label, font=axis_font, fill=_TEXT_MUTED)
        draw.line((cx, plot_top + plot_height + 2, cx, plot_top + plot_height + 10), fill=_BORDER, width=1)

    y_step = _tick_step(len(y_values))
    for idx, value in enumerate(y_values):
        if idx % y_step != 0 and idx != len(y_values) - 1:
            continue
        _, cy = _cell_center(plot_left, plot_top, cell_size, 0, idx)
        label = str(value)
        label_width, label_height = _text_size(draw, label, axis_font)
        draw.text((plot_left - label_width - 16, cy - (label_height / 2)), label, font=axis_font, fill=_TEXT_MUTED)
        draw.line((plot_left - 10, cy, plot_left - 2, cy), fill=_BORDER, width=1)

    axis_label_font = _load_font(19, bold=True)
    x_label = str(summary.get("x_label") or summary.get("x_axis") or "x")
    y_label = str(summary.get("y_label") or summary.get("y_axis") or "y")
    _draw_centered_text(draw, plot_left + (plot_width / 2), plot_top + plot_height + 46, x_label, axis_label_font, _TEXT_PRIMARY)
    _draw_rotated_text(
        image,
        x=plot_left - 74,
        y=plot_top + int((plot_height / 2) + 48),
        text=y_label,
        font=axis_label_font,
        fill=_TEXT_PRIMARY,
        angle=90,
    )


def _draw_heatmap_grid(
    draw: Any,
    *,
    summary: dict[str, Any],
    plot_left: int,
    plot_top: int,
    cell_size: int,
    limit: float,
) -> tuple[int, int]:
    rows = list(summary.get("matrix", []))
    plot_width = max(1, len(summary.get("x_values", []))) * cell_size
    plot_height = max(1, len(summary.get("y_values", []))) * cell_size
    draw.rectangle((plot_left, plot_top, plot_left + plot_width, plot_top + plot_height), fill=(244, 243, 240, 255), outline=_BORDER, width=2)

    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row.get("cells", [])):
            x0 = plot_left + (col_index * cell_size)
            y0 = plot_top + (row_index * cell_size)
            delta = cell.get("delta_suite_score")
            if delta is not None:
                delta = float(delta)
            fill = _delta_color(delta, limit)
            draw.rectangle(
                (x0, y0, x0 + cell_size, y0 + cell_size),
                fill=fill,
                outline=(255, 255, 255, 160),
                width=1,
            )

    return plot_width, plot_height


def _draw_panel(
    image: Any,
    draw: Any,
    *,
    bounds: tuple[int, int, int, int],
    summary: dict[str, Any],
    suite_id: str,
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=28, fill=_PANEL_BG, outline=_BORDER, width=2)

    title_font = _load_font(36, bold=True)
    subtitle_font = _load_font(22)
    small_font = _load_font(18)
    axis_font = _load_font(17)

    title = _panel_title(summary)
    subtitle = _panel_subtitle(summary, suite_id)
    draw.text((left + 26, top + 20), title, font=title_font, fill=_TEXT_PRIMARY)
    draw.text((left + 26, top + 64), subtitle, font=subtitle_font, fill=_TEXT_MUTED)

    header_height = 122
    available_width = right - left - 52
    available_height = bottom - top - header_height - 38

    legend_width = min(340, int(available_width * 0.36))
    colorbar_width = 28
    colorbar_gap = 20
    plot_gap = 24
    rows = max(1, len(summary.get("y_values", [])))
    cols = max(1, len(summary.get("x_values", [])))

    plot_available_width = available_width - legend_width - plot_gap - colorbar_width - colorbar_gap - 18
    plot_available_height = available_height - 110
    cell_size = max(10, min(int(plot_available_width / cols), int(plot_available_height / rows)))
    plot_width = cols * cell_size
    plot_height = rows * cell_size

    plot_left = left + 74
    plot_top = top + header_height
    limit = _clipped_limit(summary)
    plot_width, plot_height = _draw_heatmap_grid(
        draw,
        summary=summary,
        plot_left=plot_left,
        plot_top=plot_top,
        cell_size=cell_size,
        limit=limit,
    )

    baseline = summary.get("baseline", {})
    if isinstance(baseline, dict):
        baseline_col, baseline_row = _marker_index(summary, baseline)
        if baseline_col is not None and baseline_row is not None:
            cx, cy = _cell_center(plot_left, plot_top, cell_size, baseline_col, baseline_row)
            _draw_marker(draw, cx=cx, cy=cy, radius=max(5, int(cell_size * 0.22)), outline=_BASELINE_MARK, width=3)

    best_cell = summary.get("best_cell", {})
    if isinstance(best_cell, dict):
        best_col, best_row = _marker_index(summary, best_cell)
        if best_col is not None and best_row is not None:
            cx, cy = _cell_center(plot_left, plot_top, cell_size, best_col, best_row)
            _draw_marker(draw, cx=cx, cy=cy, radius=max(6, int(cell_size * 0.24)), outline=_BEST_MARK, width=4)

    _draw_axis_ticks(
        image,
        draw,
        plot_left=plot_left,
        plot_top=plot_top,
        plot_width=plot_width,
        plot_height=plot_height,
        cell_size=cell_size,
        summary=summary,
        axis_font=axis_font,
    )

    legend_left = plot_left + plot_width + 24
    legend_top = top + 118
    legend_right = right - 22
    legend_bottom = legend_top + 92
    _draw_legend_box(
        draw,
        box=(legend_left, legend_top, legend_right, legend_bottom),
        summary=summary,
        clip_limit=limit,
        small_font=small_font,
    )

    colorbar_x = legend_left + 20
    colorbar_y = legend_bottom + 26
    colorbar_height = max(200, min(420, plot_height - 30))
    _draw_colorbar(
        draw,
        x=colorbar_x,
        y=colorbar_y,
        width=colorbar_width,
        height=colorbar_height,
        limit=limit,
        small_font=small_font,
    )

    meta = _channel_meta(summary)
    if meta:
        seeds = meta.get("seeds", [])
        meta_line = f"Task: {meta.get('task_type', '-')} | Seeds: {', '.join(str(seed) for seed in seeds) if seeds else '-'}"
        draw.text((legend_left, colorbar_y + colorbar_height + 54), meta_line, font=small_font, fill=_TEXT_MUTED)


def _draw_info_box(
    draw: Any,
    *,
    box: tuple[int, int, int, int],
    title: str,
    body_lines: list[str],
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=24, fill=_PANEL_BG, outline=_BORDER, width=2)
    title_font = _load_font(24, bold=True)
    body_font = _load_font(19)
    draw.text((left + 22, top + 18), title, font=title_font, fill=_TEXT_PRIMARY)
    draw.multiline_text((left + 22, top + 58), "\n".join(body_lines), font=body_font, fill=_TEXT_MUTED, spacing=8)


def _write_annotated_single_panel_png(path: Path, summary: dict[str, Any], *, suite_id: str, figure_title: str) -> None:
    if not _PIL_AVAILABLE:
        _write_heatmap_png_fallback(path, {**summary, "suite_id": suite_id}, title=figure_title)
        return

    width = 1220
    height = 980
    image = Image.new("RGBA", (width, height), _FIG_BG)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(42, bold=True)
    subtitle_font = _load_font(21)

    _draw_centered_text(draw, width / 2, 26, figure_title, title_font, _TEXT_PRIMARY)
    subtitle = "Annotated heat map | clearer labels, markers, and clipped delta legend"
    _draw_centered_text(draw, width / 2, 78, subtitle, subtitle_font, _TEXT_MUTED)

    _draw_panel(
        image,
        draw,
        bounds=(46, 128, width - 46, height - 220),
        summary=summary,
        suite_id=suite_id,
    )

    body_lines = [
        "x = End Layer (j): duplicated block end coordinate.",
        "y = Start Layer (i): duplicated block start coordinate.",
        "Gray cells are invalid or not run. Red beats baseline. Blue trails baseline.",
        "Black ring = baseline reference when a baseline coordinate exists. Green ring = best cell.",
    ]
    _draw_info_box(
        draw,
        box=(46, height - 178, width - 46, height - 38),
        title="How To Read / 圖怎麼看",
        body_lines=body_lines,
    )

    image.save(path, format="PNG")


def _write_overview_heatmap_png(
    path: Path,
    *,
    suite_id: str,
    summary: dict[str, Any],
    base_config_id: str,
) -> None:
    if not _PIL_AVAILABLE:
        combined_summary = _channel_projection(summary, "combined")
        _write_heatmap_png_fallback(path, {**combined_summary, "suite_id": suite_id}, title=f"Heat Map | {base_config_id} | {suite_id}")
        return

    channels = summary.get("channels", {})
    projected_panels = []
    if isinstance(channels, dict) and "probe_a" in channels:
        projected_panels.append(_channel_projection(summary, "probe_a"))
    if isinstance(channels, dict) and "probe_b" in channels:
        projected_panels.append(_channel_projection(summary, "probe_b"))
    if not projected_panels:
        projected_panels.append(_channel_projection(summary, "combined"))

    panel_count = len(projected_panels)
    panel_width = 820 if panel_count > 1 else 980
    panel_gap = 34
    margin_x = 44
    width = (margin_x * 2) + (panel_width * panel_count) + (panel_gap * max(0, panel_count - 1))
    height = 1120
    image = Image.new("RGBA", (width, height), _FIG_BG)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(50, bold=True)
    subtitle_font = _load_font(24)
    meta_font = _load_font(20)

    _draw_centered_text(draw, width / 2, 20, base_config_id, title_font, _TEXT_PRIMARY)
    subtitle = "RYS-style probe heat map | Difference from Baseline (Clipped)"
    _draw_centered_text(draw, width / 2, 78, subtitle, subtitle_font, _TEXT_MUTED)

    scan = summary.get("scan", {}) if isinstance(summary.get("scan"), dict) else {}
    meta_text = (
        f"Suite {suite_id} | i = {scan.get('start_layer_min', '-')}..{scan.get('end_layer_max', '-')} | "
        f"block length = {scan.get('min_block_len', '-')}..{scan.get('max_block_len', '-')} | "
        f"repeat = {summary.get('repeat_count', '-')}"
    )
    _draw_centered_text(draw, width / 2, 112, meta_text, meta_font, _TEXT_MUTED)

    panel_top = 158
    panel_bottom = 840
    for index, projected in enumerate(projected_panels):
        left = margin_x + (index * (panel_width + panel_gap))
        _draw_panel(
            image,
            draw,
            bounds=(left, panel_top, left + panel_width, panel_bottom),
            summary=projected,
            suite_id=suite_id,
        )

    best_cell = summary.get("best_cell", {})
    best_coord = (
        f"({best_cell.get('start_layer', best_cell.get('y_value', '-'))}, "
        f"{best_cell.get('end_layer', best_cell.get('x_value', '-'))})"
        if isinstance(best_cell, dict) and best_cell
        else "-"
    )
    bottom_top = 874
    half_gap = 18
    left_box = (margin_x, bottom_top, int((width / 2) - half_gap), height - 34)
    right_box = (int((width / 2) + half_gap), bottom_top, width - margin_x, height - 34)

    probe_a = summary.get("probe_a", {}) if isinstance(summary.get("probe_a"), dict) else {}
    probe_b = summary.get("probe_b", {}) if isinstance(summary.get("probe_b"), dict) else {}
    how_to_read = [
        "x = End Layer (j): duplicated block end coordinate.",
        "y = Start Layer (i): duplicated block start coordinate.",
        "Gray cells are invalid or not run. Red is better than baseline. Blue is worse.",
        "Green ring marks the best cell for that probe. Colors are clipped for readability.",
    ]
    _draw_info_box(
        draw,
        box=left_box,
        title="How To Read / 圖怎麼看",
        body_lines=how_to_read,
    )

    summary_lines = [
        _metric_line("Baseline combined score: ", summary.get("baseline", {}).get("combined", {}).get("suite_score") if isinstance(summary.get("baseline", {}).get("combined", {}), dict) else summary.get("baseline", {}).get("suite_score_c")),
        _metric_line("Best combined score: ", best_cell.get("combined", {}).get("suite_score") if isinstance(best_cell.get("combined", {}), dict) else best_cell.get("suite_score_c")),
        _metric_line("Best combined delta: ", best_cell.get("combined", {}).get("delta_suite_score") if isinstance(best_cell.get("combined", {}), dict) else best_cell.get("delta_suite_score"), signed=True),
        f"Best cell (i, j): {best_coord}",
        f"Probe A: {probe_a.get('label', 'Probe A')} | {probe_a.get('task_type', '-')}",
        f"Probe B: {probe_b.get('label', 'Probe B')} | {probe_b.get('task_type', '-')}",
    ]
    _draw_info_box(
        draw,
        box=right_box,
        title="Scan Summary / 掃描摘要",
        body_lines=summary_lines,
    )

    image.save(path, format="PNG")


def _write_heat_map_readme(
    *,
    path: Path,
    suite_id: str,
    summary: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    best_cell = summary.get("best_cell", {}) if isinstance(summary.get("best_cell"), dict) else {}
    probe_a = summary.get("probe_a", {}) if isinstance(summary.get("probe_a"), dict) else {}
    probe_b = summary.get("probe_b", {}) if isinstance(summary.get("probe_b"), dict) else {}
    scan = summary.get("scan", {}) if isinstance(summary.get("scan"), dict) else {}
    channels = manifest.get("channel_artifacts", {}) if isinstance(manifest.get("channel_artifacts"), dict) else {}

    best_combined_delta = _format_metric(
        best_cell.get("combined", {}).get("delta_suite_score")
        if isinstance(best_cell.get("combined", {}), dict)
        else best_cell.get("delta_suite_score")
    )
    lines = [f"# Heat Map Report / 熱圖報告: {suite_id}", "", "## TL;DR / 快速摘要", ""]
    _append_bilingual(
        lines,
        "Open `heatmap.png` first. It is the side-by-side overview for Probe A and Probe B.",
        "先看 `heatmap.png`，它是 Probe A 和 Probe B 的左右對照總覽圖。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"Best cell is `i={best_cell.get('start_layer', best_cell.get('y_value', '-'))}, j={best_cell.get('end_layer', best_cell.get('x_value', '-'))}` with combined delta `{best_combined_delta}`.",
        f"最佳 cell 是 `i={best_cell.get('start_layer', best_cell.get('y_value', '-'))}, j={best_cell.get('end_layer', best_cell.get('x_value', '-'))}`，combined delta 為 `{best_combined_delta}`。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "`summary.json` contains the full machine-readable matrix, channels, baseline, and top candidates.",
        "`summary.json` 內含完整可機讀矩陣、channels、baseline 與 top candidates。",
        bullet=True,
    )
    lines.extend(["", "## Quick Start / 先看哪裡", ""])
    _append_bilingual(
        lines,
        "`heatmap.png`: side-by-side overview for Probe A and Probe B. This is the best first stop.",
        "`heatmap.png`：Probe A 與 Probe B 的左右對照總覽，最建議先看這張。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "`combined/heatmap.png`: annotated single-panel view for the combined score.",
        "`combined/heatmap.png`：combined score 的單張詳解圖。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"`probe_a/heatmap.png`: annotated single-panel view for `{probe_a.get('label', 'Probe A')}`.",
        f"`probe_a/heatmap.png`：`{probe_a.get('label', 'Probe A')}` 的單張詳解圖。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"`probe_b/heatmap.png`: annotated single-panel view for `{probe_b.get('label', 'Probe B')}`.",
        f"`probe_b/heatmap.png`：`{probe_b.get('label', 'Probe B')}` 的單張詳解圖。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "`summary.json`: full machine-readable result, including baseline, best cell, top candidates, matrix, and channels.",
        "`summary.json`：完整機器可讀結果，含 baseline、best cell、top candidates、matrix 與 channels。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "`top_candidates.csv`: quick table of the strongest cells.",
        "`top_candidates.csv`：強勢 cells 的快速表格。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "`cells.csv`: all scanned cells in flat-table form.",
        "`cells.csv`：所有掃描過的 cells 平面表格。",
        bullet=True,
    )
    lines.extend(["", "## Scan Setup / 掃描設定", ""])
    _append_bilingual(lines, f"Heat-map type: `{summary.get('heat_map_type', '-')}`", f"熱圖類型：`{summary.get('heat_map_type', '-')}`", bullet=True)
    _append_bilingual(
        lines,
        f"Start layer range: `{scan.get('start_layer_min', '-')}` to `{scan.get('end_layer_max', '-')}`",
        f"起始 layer 範圍：`{scan.get('start_layer_min', '-')}` 到 `{scan.get('end_layer_max', '-')}`",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"Block length range: `{scan.get('min_block_len', '-')}` to `{scan.get('max_block_len', '-')}`",
        f"區塊長度範圍：`{scan.get('min_block_len', '-')}` 到 `{scan.get('max_block_len', '-')}`",
        bullet=True,
    )
    _append_bilingual(lines, f"Repeat count: `{summary.get('repeat_count', '-')}`", f"重複次數：`{summary.get('repeat_count', '-')}`", bullet=True)
    _append_bilingual(lines, f"Cell count: `{summary.get('cell_count', '-')}`", f"Cell 數量：`{summary.get('cell_count', '-')}`", bullet=True)
    _append_bilingual(
        lines,
        f"Probe A: `{probe_a.get('label', 'Probe A')}` / `{probe_a.get('task_type', '-')}` / seeds `{probe_a.get('seeds', [])}`",
        f"Probe A：`{probe_a.get('label', 'Probe A')}` / `{probe_a.get('task_type', '-')}` / seeds `{probe_a.get('seeds', [])}`",
        bullet=True,
    )
    _append_bilingual(
        lines,
        f"Probe B: `{probe_b.get('label', 'Probe B')}` / `{probe_b.get('task_type', '-')}` / seeds `{probe_b.get('seeds', [])}`",
        f"Probe B：`{probe_b.get('label', 'Probe B')}` / `{probe_b.get('task_type', '-')}` / seeds `{probe_b.get('seeds', [])}`",
        bullet=True,
    )
    lines.extend(["", "## Coordinates / 座標怎麼看", ""])
    _append_bilingual(
        lines,
        "`x = End Layer (j)`: the end coordinate of the duplicated layer window.",
        "`x = End Layer (j)`：duplicated layer window 的結束座標。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "`y = Start Layer (i)`: the start coordinate of the duplicated layer window.",
        "`y = Start Layer (i)`：duplicated layer window 的起始座標。",
        bullet=True,
    )
    _append_bilingual(
        lines,
        "One cell stands for duplicating the block from `i..j` once per `repeat_count`.",
        "每一個 cell 代表把 `i..j` 這段 block 依 `repeat_count` 重複插入。",
        bullet=True,
    )
    _append_bilingual(lines, "`block_len = j - i + 1`.", "`block_len = j - i + 1`。", bullet=True)
    _append_bilingual(lines, "Gray cells are invalid or were not run.", "灰色 cells 代表無效或未執行。", bullet=True)
    lines.extend(["", "## Result Interpretation / 結果怎麼解讀", ""])
    _append_bilingual(lines, "Red means the score is better than baseline. Blue means it is worse.", "紅色代表比 baseline 更好，藍色代表比 baseline 更差。", bullet=True)
    _append_bilingual(lines, "Color is clipped for readability, so extreme outliers do not wash out the whole plot.", "顏色做過 clipping，避免極端值把整張圖的對比洗掉。", bullet=True)
    _append_bilingual(lines, "Black ring marks the baseline location when a baseline coordinate exists.", "黑色圓圈代表 baseline 位置；前提是 baseline 本身有座標。", bullet=True)
    _append_bilingual(lines, "Green ring marks the best cell for that probe or channel.", "綠色圓圈代表該 probe 或 channel 的最佳 cell。", bullet=True)
    _append_bilingual(
        lines,
        f"Best cell: `i={best_cell.get('start_layer', best_cell.get('y_value', '-'))}, j={best_cell.get('end_layer', best_cell.get('x_value', '-'))}`",
        f"最佳 cell：`i={best_cell.get('start_layer', best_cell.get('y_value', '-'))}, j={best_cell.get('end_layer', best_cell.get('x_value', '-'))}`",
        bullet=True,
    )
    _append_bilingual(lines, f"Best combined delta: `{best_combined_delta}`", f"最佳 combined delta：`{best_combined_delta}`", bullet=True)

    if channels:
        lines.extend(["", "## File Map / 檔案索引", ""])
        _append_bilingual(lines, f"Overview PNG: `{manifest.get('png_path', '-')}`", f"總覽 PNG：`{manifest.get('png_path', '-')}`", bullet=True)
        _append_bilingual(lines, f"Summary JSON: `{manifest.get('summary_path', '-')}`", f"摘要 JSON：`{manifest.get('summary_path', '-')}`", bullet=True)
        _append_bilingual(lines, f"Top candidates CSV: `{manifest.get('top_candidates_csv_path', '-')}`", f"Top candidates CSV：`{manifest.get('top_candidates_csv_path', '-')}`", bullet=True)
        _append_bilingual(lines, f"Combined channel PNG: `{channels.get('combined', {}).get('png_path', '-')}`", f"Combined channel PNG：`{channels.get('combined', {}).get('png_path', '-')}`", bullet=True)
        _append_bilingual(lines, f"Probe A channel PNG: `{channels.get('probe_a', {}).get('png_path', '-')}`", f"Probe A channel PNG：`{channels.get('probe_a', {}).get('png_path', '-')}`", bullet=True)
        _append_bilingual(lines, f"Probe B channel PNG: `{channels.get('probe_b', {}).get('png_path', '-')}`", f"Probe B channel PNG：`{channels.get('probe_b', {}).get('png_path', '-')}`", bullet=True)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_heat_map_artifacts(
    *,
    output_dir: Path,
    suite_id: str,
    heat_map_summary: dict[str, Any],
    created_at: str,
    base_config_id: str,
    selected_config_id: str,
    status: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    matrix_path = output_dir / "matrix.csv"
    cells_path = output_dir / "cells.csv"
    top_candidates_json_path = output_dir / "top_candidates.json"
    top_candidates_csv_path = output_dir / "top_candidates.csv"
    summary_path = output_dir / "summary.json"
    png_path = output_dir / "heatmap.png"
    manifest_path = output_dir / "manifest.json"
    readme_path = output_dir / "README.md"

    combined_summary = _channel_projection(heat_map_summary, "combined")
    _write_matrix_csv(matrix_path, combined_summary)
    _write_cells_csv(cells_path, combined_summary)
    _write_top_candidates_csv(top_candidates_csv_path, combined_summary)
    write_json(top_candidates_json_path, {"top_candidates": combined_summary.get("top_candidates", [])})
    write_json(summary_path, heat_map_summary)
    _write_overview_heatmap_png(
        png_path,
        suite_id=suite_id,
        summary=heat_map_summary,
        base_config_id=base_config_id,
    )

    channel_artifacts: dict[str, dict[str, str]] = {}
    channels = heat_map_summary.get("channels", {})
    if isinstance(channels, dict):
        for channel_id in ("combined", "probe_a", "probe_b"):
            if channel_id not in channels:
                continue
            projected = _channel_projection(heat_map_summary, channel_id)
            channel_dir = output_dir / channel_id
            channel_dir.mkdir(parents=True, exist_ok=True)
            channel_matrix_path = channel_dir / "matrix.csv"
            channel_cells_path = channel_dir / "cells.csv"
            channel_top_candidates_csv_path = channel_dir / "top_candidates.csv"
            channel_top_candidates_json_path = channel_dir / "top_candidates.json"
            channel_summary_path = channel_dir / "summary.json"
            channel_png_path = channel_dir / "heatmap.png"
            _write_matrix_csv(channel_matrix_path, projected)
            _write_cells_csv(channel_cells_path, projected)
            _write_top_candidates_csv(channel_top_candidates_csv_path, projected)
            write_json(channel_top_candidates_json_path, {"top_candidates": projected.get("top_candidates", [])})
            write_json(channel_summary_path, projected)
            _write_annotated_single_panel_png(
                channel_png_path,
                {**projected, "suite_id": suite_id},
                suite_id=suite_id,
                figure_title=f"{base_config_id} | {projected.get('channel_label', channel_id)}",
            )
            channel_artifacts[channel_id] = {
                "matrix_path": str(channel_matrix_path.resolve()),
                "cells_path": str(channel_cells_path.resolve()),
                "top_candidates_json_path": str(channel_top_candidates_json_path.resolve()),
                "top_candidates_csv_path": str(channel_top_candidates_csv_path.resolve()),
                "summary_path": str(channel_summary_path.resolve()),
                "png_path": str(channel_png_path.resolve()),
            }

    manifest = {
        "suite_id": suite_id,
        "created_at": created_at,
        "status": status,
        "base_config_id": base_config_id,
        "selected_config_id": selected_config_id,
        "x_axis": heat_map_summary.get("x_axis"),
        "x_label": heat_map_summary.get("x_label"),
        "y_axis": heat_map_summary.get("y_axis"),
        "y_label": heat_map_summary.get("y_label"),
        "cell_count": heat_map_summary.get("cell_count"),
        "improved_cell_count": heat_map_summary.get("improved_cell_count"),
        "matrix_path": str(matrix_path.resolve()),
        "cells_path": str(cells_path.resolve()),
        "top_candidates_json_path": str(top_candidates_json_path.resolve()),
        "top_candidates_csv_path": str(top_candidates_csv_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "png_path": str(png_path.resolve()),
        "channel_artifacts": channel_artifacts,
    }
    _write_heat_map_readme(
        path=readme_path,
        suite_id=suite_id,
        summary=heat_map_summary,
        manifest=manifest,
    )
    manifest["readme_path"] = str(readme_path.resolve())
    write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path.resolve())
    manifest["output_dir"] = str(output_dir.resolve())
    return manifest
