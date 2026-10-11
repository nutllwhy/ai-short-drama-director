#!/usr/bin/env python3
"""Validate an AI short-drama pre-production manifest and its local assets."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Any


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
NO_BGM_MARKERS = ("无背景音乐", "无bgm", "无 bgm", "no background music")
FEISHU_URL_RE = re.compile(
    r"^https://(?:[a-z0-9-]+\.)*(?:feishu\.cn|larksuite\.com)(?::\d+)?/"
    r"(?P<kind>docx|wiki)/(?P<token>[a-z0-9_-]+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
DOCUMENT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
DELIVERY_CLASSIFIED_COUNT_FIELDS = (
    "character_card_count",
    "turnaround_count",
    "identity_image_count",
    "system_ui_count",
    "scene_card_count",
    "scene_anchor_count",
    "scene_variant_count",
    "voice_sample_count",
    "pending_voice_count",
    "segment_row_count",
    "prompt_count",
    "archive_count",
)
CHARACTER_CLASSES = {
    "protagonist",
    "antagonist",
    "recurring_supporting",
    "one_off_named",
    "system_entity",
}
SCENE_CLASSES = {"core", "minor", "variant"}
IMAGE_ASSET_TYPES = {
    "character_turnaround",
    "character_identity",
    "system_ui_anchor",
    "scene_anchor",
    "scene_variant",
    "storyboard",
    "prop_reference",
}
INVENTORY_ASSET_ROLES = {
    "story_contract",
    "confirmed_outline",
    "complete_script",
    "continuity_ledger",
    "character_card",
    "character_image",
    "character_image_prompt",
    "system_ui_image",
    "system_ui_prompt",
    "scene_card",
    "scene_image",
    "scene_image_prompt",
    "storyboard_image",
    "prop_image",
    "voice_sample",
    "segment_generation_table",
    "segment_prompt",
    "validation_result",
    "supporting_file",
}
GLOBAL_REQUIRED_ROLES = {
    "story_contract",
    "confirmed_outline",
    "complete_script",
    "continuity_ledger",
    "segment_generation_table",
    "validation_result",
}
DELIVERY_RECEIPT_FILENAME = "飞书交付回执.json"
LEGACY_DELIVERY_RECEIPT_VERSION = 1
HARDENED_DELIVERY_RECEIPT_VERSION = 2
VISUAL_EVIDENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}

CHARACTER_INVENTORY_ROLES = {
    "character_card",
    "character_image",
    "character_image_prompt",
    "system_ui_image",
    "system_ui_prompt",
}
SCENE_INVENTORY_ROLES = {"scene_card", "scene_image", "scene_image_prompt"}


def reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a JSON object")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid_rfc3339(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def zip_file_count(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(1 for item in archive.infolist() if not item.is_dir())


def image_magic_error(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:16]
    except OSError as exc:
        return f"cannot read image header: {exc}"
    suffix = path.suffix.lower()
    valid = (
        (suffix == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and head.startswith(b"\xff\xd8\xff"))
        or (suffix == ".webp" and len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        return "unsupported image extension; use .png, .jpg, .jpeg, or .webp"
    if not valid:
        return f"file header does not match image extension {suffix}"
    return None


def audio_magic_error(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:16]
    except OSError as exc:
        return f"cannot read audio header: {exc}"
    suffix = path.suffix.lower()
    valid = (
        (suffix == ".wav" and len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE")
        or (suffix == ".mp3" and (head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)))
        or (suffix in {".m4a", ".mp4"} and len(head) >= 12 and head[4:8] == b"ftyp")
        or (suffix == ".aac" and len(head) >= 2 and head[0] == 0xFF and head[1] & 0xF0 == 0xF0)
        or (suffix == ".flac" and head.startswith(b"fLaC"))
        or (suffix == ".ogg" and head.startswith(b"OggS"))
    )
    if suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg"}:
        return "unsupported audio extension"
    if not valid:
        return f"file header does not match audio extension {suffix}"
    return None


def visual_evidence_magic_error(path: Path) -> str | None:
    if path.suffix.lower() == ".pdf":
        try:
            if not path.read_bytes()[:5].startswith(b"%PDF-"):
                return "file header does not match .pdf"
        except OSError as exc:
            return f"cannot read visual evidence header: {exc}"
        return None
    return image_magic_error(path)


def safe_relative_path(value: Any, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        add(errors, f"{label} must be a non-empty relative path")
        return None
    normalized = value.strip().replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", normalized) or ".." in path.parts:
        add(errors, f"{label} must stay inside the project root")
        return None
    return path.as_posix()


def relative_to_root(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def duration_marker_present(prompt: str, duration: float) -> bool:
    value = f"{duration:g}"
    return re.search(rf"(?<![\d.]){re.escape(value)}\s*秒", prompt) is not None


def normalized_text_sha256(value: str) -> str:
    """Hash text after the normalization used for Feishu prompt readback."""
    normalized = unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n").strip(),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def asset_path(root: Path, folder: str, filename: Any) -> Path | None:
    if filename is None:
        return None
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename must be a non-empty relative path")
    relative = Path(filename)
    if relative.is_absolute():
        raise ValueError("absolute paths are not allowed")
    resolved_root = root.resolve()
    base = (resolved_root / folder).resolve()
    try:
        base.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"resource directory escapes project root: {folder}") from exc
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path escapes {folder}/") from exc
    return candidate


def add(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def collect_references(segments: list[Any], field: str) -> set[str]:
    result: set[str] = set()
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        values = segment.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str):
                result.add(value)
    return result


def collect_asset_references(items: dict[str, Any], field: str) -> set[str]:
    """Collect string asset IDs from dictionary entries."""
    result: set[str] = set()
    for item in items.values():
        if not isinstance(item, dict):
            continue
        values = item.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str):
                result.add(value)
    return result


def collect_segment_ids(segments: list[Any]) -> set[str]:
    """Collect normalized, syntactically valid segment IDs."""
    result: set[str] = set()
    for item in segments:
        if not isinstance(item, dict):
            continue
        value = item.get("id")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            continue
        normalized = str(value).strip()
        if normalized:
            result.add(normalized)
    return result


def validate_string_id_array(
    value: Any,
    label: str,
    errors: list[str],
) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        add(errors, f"{label} must be a non-empty array of string IDs")
        return []
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        add(errors, f"{label} contains duplicate IDs")
    return normalized


def expected_delivery_counts(
    segments: list[Any],
    images: dict[str, Any],
    voices: dict[str, Any],
    inventory_by_path: dict[str, dict[str, Any]],
) -> dict[str, int]:
    role_counts = Counter(
        item.get("asset_role")
        for item in inventory_by_path.values()
        if isinstance(item, dict) and isinstance(item.get("asset_role"), str)
    )
    image_type_counts = Counter(
        item.get("asset_type")
        for item in images.values()
        if isinstance(item, dict) and isinstance(item.get("asset_type"), str)
    )
    ready_voice_count = sum(
        1 for item in voices.values() if isinstance(item, dict) and item.get("ready") is True
    )
    pending_voice_count = sum(
        1 for item in voices.values() if isinstance(item, dict) and item.get("ready") is False
    )
    return {
        "character_card_count": role_counts["character_card"],
        "turnaround_count": image_type_counts["character_turnaround"],
        "identity_image_count": image_type_counts["character_identity"],
        "system_ui_count": image_type_counts["system_ui_anchor"],
        "scene_card_count": role_counts["scene_card"],
        "scene_anchor_count": image_type_counts["scene_anchor"],
        "scene_variant_count": image_type_counts["scene_variant"],
        "voice_sample_count": ready_voice_count,
        "pending_voice_count": pending_voice_count,
        "segment_row_count": len(segments),
        "prompt_count": role_counts["segment_prompt"],
        "archive_count": 1,
    }


def validate_block_id_mapping(
    value: Any,
    expected_ids: set[str],
    label: str,
    blockers: list[str],
) -> None:
    if not isinstance(value, dict):
        add(blockers, f"delivery: readback_evidence {label} must be an object keyed by manifest ID")
        return
    actual_ids = {key for key in value if isinstance(key, str)}
    if len(actual_ids) != len(value) or actual_ids != expected_ids:
        add(
            blockers,
            f"delivery: readback_evidence {label} must map every manifest ID exactly once",
        )
    block_ids: list[str] = []
    for asset_id, block_id in value.items():
        if not isinstance(block_id, str) or not block_id.strip():
            add(
                blockers,
                f"delivery: readback_evidence {label}.{asset_id} must be a non-empty block identifier",
            )
        else:
            block_ids.append(block_id.strip())
    if len(block_ids) != len(set(block_ids)):
        add(blockers, f"delivery: readback_evidence {label} contains duplicate block identifiers")


def validate_hash(path: Path, expected: Any, label: str, errors: list[str]) -> None:
    if expected is None:
        return
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        add(errors, f"{label}: sha256 must be 64 hexadecimal characters")
        return
    try:
        actual = sha256_file(path)
    except OSError as exc:
        add(errors, f"{label}: cannot read file for sha256: {exc}")
        return
    if actual.lower() != expected.lower():
        add(errors, f"{label}: sha256 mismatch: {path.name}")


def validate_segment_readback(
    value: Any,
    segments: list[Any],
    segment_row_ids: Any,
    blockers: list[str],
) -> None:
    """Bind each remote Feishu row to the manifest segment and exact prompt text."""
    segment_by_id = {
        str(segment.get("id")).strip(): segment
        for segment in segments
        if isinstance(segment, dict)
        and not isinstance(segment.get("id"), bool)
        and isinstance(segment.get("id"), (str, int))
        and str(segment.get("id")).strip()
    }
    if not isinstance(value, dict):
        add(blockers, "delivery: segment_readback must be an object keyed by segment ID")
        return
    if {key for key in value if isinstance(key, str)} != set(segment_by_id) or len(value) != len(segment_by_id):
        add(blockers, "delivery: segment_readback must cover every manifest segment ID exactly once")

    row_mapping = segment_row_ids if isinstance(segment_row_ids, dict) else {}
    seen_row_ids: list[str] = []
    seen_prompt_block_ids: list[str] = []
    for segment_id, segment in segment_by_id.items():
        record = value.get(segment_id)
        if not isinstance(record, dict):
            add(blockers, f"delivery: segment_readback.{segment_id} must be an object")
            continue

        row_id = record.get("row_id")
        if not isinstance(row_id, str) or not row_id.strip():
            add(blockers, f"delivery: segment_readback.{segment_id}.row_id must be non-empty")
        else:
            normalized_row_id = row_id.strip()
            seen_row_ids.append(normalized_row_id)
            expected_row_id = row_mapping.get(segment_id)
            if isinstance(expected_row_id, str) and expected_row_id.strip() != normalized_row_id:
                add(blockers, f"delivery: segment_readback.{segment_id}.row_id does not match readback evidence")

        prompt_block_id = record.get("prompt_block_id")
        if not isinstance(prompt_block_id, str) or not prompt_block_id.strip():
            add(blockers, f"delivery: segment_readback.{segment_id}.prompt_block_id must be non-empty")
        else:
            seen_prompt_block_ids.append(prompt_block_id.strip())

        prompt_sha = record.get("prompt_sha256")
        manifest_prompt = segment.get("prompt")
        if not isinstance(prompt_sha, str) or not SHA256_RE.fullmatch(prompt_sha):
            add(blockers, f"delivery: segment_readback.{segment_id}.prompt_sha256 must be 64 hexadecimal characters")
        elif not isinstance(manifest_prompt, str) or prompt_sha.lower() != normalized_text_sha256(manifest_prompt):
            add(blockers, f"delivery: segment_readback.{segment_id}.prompt_sha256 does not match manifest prompt")

        duration = record.get("duration_seconds")
        expected_duration = segment.get("duration_seconds")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or float(duration) <= 0
        ):
            add(blockers, f"delivery: segment_readback.{segment_id}.duration_seconds must be a positive finite number")
        elif (
            isinstance(expected_duration, bool)
            or not isinstance(expected_duration, (int, float))
            or abs(float(duration) - float(expected_duration)) > 1e-6
        ):
            add(blockers, f"delivery: segment_readback.{segment_id}.duration_seconds does not match manifest")

        aspect_ratio = record.get("aspect_ratio")
        if not isinstance(aspect_ratio, str) or not aspect_ratio.strip():
            add(blockers, f"delivery: segment_readback.{segment_id}.aspect_ratio must be non-empty")
        elif aspect_ratio.strip() != str(segment.get("aspect_ratio", "")).strip():
            add(blockers, f"delivery: segment_readback.{segment_id}.aspect_ratio does not match manifest")

        for field, manifest_field in (
            ("image_upload_order", "images"),
            ("voice_upload_order", "voices"),
        ):
            actual_order = record.get(field)
            expected_order = segment.get(manifest_field)
            if not isinstance(actual_order, list) or any(not isinstance(item, str) for item in actual_order):
                add(blockers, f"delivery: segment_readback.{segment_id}.{field} must be an array of IDs")
            elif not isinstance(expected_order, list) or actual_order != expected_order:
                add(blockers, f"delivery: segment_readback.{segment_id}.{field} does not match manifest order")

    if len(seen_row_ids) != len(set(seen_row_ids)):
        add(blockers, "delivery: segment_readback contains duplicate row_id values")
    if len(seen_prompt_block_ids) != len(set(seen_prompt_block_ids)):
        add(blockers, "delivery: segment_readback contains duplicate prompt_block_id values")


def validate_voice_playback_verification(
    value: Any,
    voices: dict[str, Any],
    voice_block_ids: Any,
    blockers: list[str],
) -> None:
    """Require playback proof for ready voices and explicit state blocks for pending voices."""
    expected_ids = {str(voice_id) for voice_id in voices}
    if not isinstance(value, dict):
        add(blockers, "delivery: voice_playback_verification must be an object keyed by voice ID")
        return
    if {key for key in value if isinstance(key, str)} != expected_ids or len(value) != len(expected_ids):
        add(blockers, "delivery: voice_playback_verification must cover every manifest voice ID exactly once")
    block_mapping = voice_block_ids if isinstance(voice_block_ids, dict) else {}
    media_tokens: list[str] = []
    for voice_id, voice in voices.items():
        normalized_id = str(voice_id)
        record = value.get(normalized_id)
        if not isinstance(record, dict):
            add(blockers, f"delivery: voice_playback_verification.{normalized_id} must be an object")
            continue
        block_id = record.get("block_id")
        if not isinstance(block_id, str) or not block_id.strip():
            add(blockers, f"delivery: voice_playback_verification.{normalized_id}.block_id must be non-empty")
        else:
            expected_block_id = block_mapping.get(normalized_id)
            if isinstance(expected_block_id, str) and expected_block_id.strip() != block_id.strip():
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.block_id does not match readback evidence")

        ready = isinstance(voice, dict) and voice.get("ready") is True
        if ready:
            if record.get("status") != "played":
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.status must be played")
            if not is_valid_rfc3339(record.get("verified_at")):
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.verified_at must be RFC 3339 with timezone")
            media_token = record.get("media_token")
            if not isinstance(media_token, str) or not media_token.strip():
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.media_token must be non-empty")
            else:
                media_tokens.append(media_token.strip())
            duration = record.get("duration_seconds")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration))
                or float(duration) <= 0
            ):
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.duration_seconds must be positive")
            for flag in ("audible", "correct_character", "natural", "no_background_music"):
                if record.get(flag) is not True:
                    add(blockers, f"delivery: voice_playback_verification.{normalized_id}.{flag} must be true")
        else:
            if record.get("status") != "pending":
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.status must be pending")
            reason = record.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                add(blockers, f"delivery: voice_playback_verification.{normalized_id}.reason must be non-empty")
    if len(media_tokens) != len(set(media_tokens)):
        add(blockers, "delivery: voice_playback_verification contains duplicate media_token values")


def validate_visual_preview_evidence(
    value: Any,
    root: Path,
    receipt_path: Path,
    document_id: Any,
    revision: Any,
    characters: dict[str, Any],
    scenes: dict[str, Any],
    images: dict[str, Any],
    voices: dict[str, Any],
    segments: list[Any],
    blockers: list[str],
    checked_files: set[Path],
) -> None:
    if not isinstance(value, dict):
        add(blockers, "delivery: visual_preview_evidence must be an object")
        return
    filename = safe_relative_path(value.get("filename"), "delivery: visual_preview_evidence filename", blockers)
    evidence_path: Path | None = None
    if filename is not None:
        try:
            evidence_path = asset_path(root, "", filename)
        except ValueError as exc:
            add(blockers, f"delivery: visual_preview_evidence {exc}")
    expected_sha = value.get("sha256")
    if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
        add(blockers, "delivery: visual_preview_evidence sha256 must be 64 hexadecimal characters")
    expected_size = value.get("size_bytes")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
        add(blockers, "delivery: visual_preview_evidence size_bytes must be a positive integer")
    if not is_valid_rfc3339(value.get("captured_at")):
        add(blockers, "delivery: visual_preview_evidence captured_at must be RFC 3339 with timezone")
    if value.get("document_id") != document_id:
        add(blockers, "delivery: visual_preview_evidence document_id does not match receipt")
    if str(value.get("revision", "")).strip() != str(revision or "").strip():
        add(blockers, "delivery: visual_preview_evidence revision does not match receipt")

    coverage = value.get("coverage")
    required_coverage = {"document_header", "archive"}
    required_coverage.update(f"character:{item}" for item in characters)
    required_coverage.update(f"scene:{item}" for item in scenes)
    required_coverage.update(f"image:{item}" for item in images)
    required_coverage.update(f"voice:{item}" for item in voices)
    required_coverage.update(f"segment:{item}" for item in collect_segment_ids(segments))
    if not isinstance(coverage, list) or any(not isinstance(item, str) or not item.strip() for item in coverage):
        add(blockers, "delivery: visual_preview_evidence coverage must be an array of non-empty labels")
    else:
        normalized_coverage = [item.strip() for item in coverage]
        if len(normalized_coverage) != len(set(normalized_coverage)):
            add(blockers, "delivery: visual_preview_evidence coverage contains duplicates")
        missing = required_coverage - set(normalized_coverage)
        if missing:
            add(blockers, f"delivery: visual_preview_evidence coverage missing {sorted(missing)}")

    if evidence_path is not None:
        if evidence_path == receipt_path:
            add(blockers, "delivery: visual_preview_evidence cannot be the receipt itself")
        elif evidence_path.suffix.lower() not in VISUAL_EVIDENCE_SUFFIXES:
            add(blockers, "delivery: visual_preview_evidence must be PNG, JPEG, WebP, or PDF")
        elif not evidence_path.is_file():
            add(blockers, f"delivery: visual_preview_evidence file not found: {evidence_path}")
        else:
            checked_files.add(evidence_path)
            magic_error = visual_evidence_magic_error(evidence_path)
            if magic_error is not None:
                add(blockers, f"delivery: visual_preview_evidence {magic_error}")
            if isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha):
                if sha256_file(evidence_path).lower() != expected_sha.lower():
                    add(blockers, "delivery: visual_preview_evidence sha256 mismatch")
            if isinstance(expected_size, int) and not isinstance(expected_size, bool):
                if evidence_path.stat().st_size != expected_size:
                    add(blockers, "delivery: visual_preview_evidence size_bytes mismatch")


def validate_delivery_receipt(
    receipt_path: Path,
    root: Path,
    manifest_path: Path,
    segments: list[Any],
    characters: dict[str, Any],
    scenes: dict[str, Any],
    images: dict[str, Any],
    voices: dict[str, Any],
    inventory_by_path: dict[str, dict[str, Any]],
    blockers: list[str],
    checked_files: set[Path],
    warnings: list[str],
    require_hardened: bool,
) -> tuple[bool, bool, int | None]:
    resolved_root = root.resolve()
    receipt_path = receipt_path.resolve()
    if receipt_path.parent != resolved_root or receipt_path.name != DELIVERY_RECEIPT_FILENAME:
        add(blockers, f"delivery: receipt must be project-root {DELIVERY_RECEIPT_FILENAME}")
        return False, False, None
    if not receipt_path.is_file():
        add(blockers, f"delivery: receipt file not found: {receipt_path}")
        return False, False, None
    try:
        receipt = read_json(receipt_path)
    except ValueError as exc:
        add(blockers, f"delivery: cannot read receipt: {exc}")
        return False, False, None
    checked_files.add(receipt_path)

    canonical_version = receipt.get("delivery_evidence_version")
    compatibility_version = receipt.get("receipt_schema_version")
    if canonical_version is not None and compatibility_version is not None and canonical_version != compatibility_version:
        add(blockers, "delivery: delivery_evidence_version and receipt_schema_version disagree")
    raw_receipt_version = (
        canonical_version
        if canonical_version is not None
        else compatibility_version
        if compatibility_version is not None
        else LEGACY_DELIVERY_RECEIPT_VERSION
    )
    if isinstance(raw_receipt_version, bool) or not isinstance(raw_receipt_version, int) or raw_receipt_version not in {
        LEGACY_DELIVERY_RECEIPT_VERSION,
        HARDENED_DELIVERY_RECEIPT_VERSION,
    }:
        add(blockers, "delivery: delivery_evidence_version must be 1 or 2")
        receipt_version = LEGACY_DELIVERY_RECEIPT_VERSION
    else:
        receipt_version = raw_receipt_version
    hardened_receipt = receipt_version == HARDENED_DELIVERY_RECEIPT_VERSION
    if require_hardened and not hardened_receipt:
        add(blockers, "delivery: hardened final gate requires delivery_evidence_version 2")
    if not hardened_receipt:
        add(
            warnings,
            "delivery receipt schema v1 is accepted for compatibility but does not prove remote prompt hashes, voice playback, cloud ZIP hash, or visual coverage",
        )

    if receipt.get("platform") != "feishu_doc":
        add(blockers, "delivery: platform must be feishu_doc")
    if receipt.get("status") != "verified":
        add(blockers, "delivery: status must be verified")

    document_url = receipt.get("document_url")
    url_match = FEISHU_URL_RE.fullmatch(document_url.strip()) if isinstance(document_url, str) else None
    if url_match is None:
        add(blockers, "delivery: document_url must be a Feishu or Lark /docx/ or /wiki/ URL")

    document_id = receipt.get("document_id")
    if not isinstance(document_id, str) or not DOCUMENT_ID_RE.fullmatch(document_id.strip()):
        add(blockers, "delivery: document_id must be a non-empty document identifier")
    elif url_match is not None and url_match.group("kind").lower() == "docx":
        if url_match.group("token") != document_id.strip():
            add(blockers, "delivery: /docx/ URL token must match document_id")

    if not is_valid_rfc3339(receipt.get("verified_at")):
        add(blockers, "delivery: verified_at must be an RFC 3339 timestamp with timezone")
    for field in ("readback_evidence_id", "revision"):
        value = receipt.get(field)
        if not isinstance(value, str) or not value.strip():
            add(blockers, f"delivery: {field} must be a non-empty remote readback identifier")

    previewed_image_ids = receipt.get("previewed_image_ids")
    if not isinstance(previewed_image_ids, list) or any(not isinstance(item, str) for item in previewed_image_ids):
        add(blockers, "delivery: previewed_image_ids must be an array of image IDs")
    else:
        if len(previewed_image_ids) != len(set(previewed_image_ids)):
            add(blockers, "delivery: previewed_image_ids contains duplicates")
        if set(previewed_image_ids) != set(images):
            add(blockers, "delivery: previewed_image_ids must contain every manifest image ID exactly once")

    evidence_data: dict[str, Any] | None = None
    evidence_filename: str | None = None
    evidence_archive_attachment_token: str | None = None
    block_ids: dict[str, Any] = {}
    evidence = receipt.get("readback_evidence")
    if not isinstance(evidence, dict):
        add(blockers, "delivery: readback_evidence must be an object")
    else:
        evidence_filename = safe_relative_path(evidence.get("filename"), "delivery: readback_evidence filename", blockers)
        evidence_path: Path | None = None
        if evidence_filename is not None:
            try:
                evidence_path = asset_path(root, "", evidence_filename)
            except ValueError as exc:
                add(blockers, f"delivery: readback_evidence {exc}")
        evidence_sha = evidence.get("sha256")
        if not isinstance(evidence_sha, str) or not SHA256_RE.fullmatch(evidence_sha):
            add(blockers, "delivery: readback_evidence sha256 must be 64 hexadecimal characters")
        evidence_size = evidence.get("size_bytes")
        if isinstance(evidence_size, bool) or not isinstance(evidence_size, int) or evidence_size <= 0:
            add(blockers, "delivery: readback_evidence size_bytes must be a positive integer")
        if evidence_path is not None:
            if evidence_path == receipt_path:
                add(blockers, "delivery: readback_evidence cannot be the receipt itself")
            elif evidence_path.suffix.lower() != ".json":
                add(blockers, "delivery: readback_evidence must point to a JSON file")
            elif not evidence_path.is_file():
                add(blockers, f"delivery: readback_evidence file not found: {evidence_path}")
            else:
                checked_files.add(evidence_path)
                if isinstance(evidence_sha, str) and SHA256_RE.fullmatch(evidence_sha):
                    if sha256_file(evidence_path).lower() != evidence_sha.lower():
                        add(blockers, "delivery: readback_evidence sha256 mismatch")
                if isinstance(evidence_size, int) and not isinstance(evidence_size, bool):
                    if evidence_path.stat().st_size != evidence_size:
                        add(blockers, "delivery: readback_evidence size_bytes mismatch")
                try:
                    evidence_data = read_json(evidence_path)
                except ValueError as exc:
                    add(blockers, f"delivery: readback_evidence JSON is invalid: {exc}")

    if evidence_data is not None:
        evidence_identity_fields = {
            "document_id": document_id,
            "readback_evidence_id": receipt.get("readback_evidence_id"),
            "revision": receipt.get("revision"),
        }
        for field, expected_value in evidence_identity_fields.items():
            actual_value = evidence_data.get(field)
            if not isinstance(actual_value, str) or not actual_value.strip():
                add(blockers, f"delivery: readback_evidence {field} must be a non-empty string")
            elif not isinstance(expected_value, str) or actual_value.strip() != expected_value.strip():
                add(blockers, f"delivery: readback_evidence {field} does not match receipt")

        raw_block_ids = evidence_data.get("block_ids")
        if raw_block_ids is None:
            block_ids = evidence_data
        elif isinstance(raw_block_ids, dict):
            block_ids = raw_block_ids
        else:
            add(blockers, "delivery: readback_evidence block_ids must be an object when supplied")
            block_ids = {}
        validate_block_id_mapping(
            block_ids.get("image_block_ids"),
            {str(image_id) for image_id in images},
            "image_block_ids",
            blockers,
        )
        validate_block_id_mapping(
            block_ids.get("voice_block_ids"),
            {str(voice_id) for voice_id in voices},
            "voice_block_ids",
            blockers,
        )
        validate_block_id_mapping(
            block_ids.get("segment_row_ids"),
            collect_segment_ids(segments),
            "segment_row_ids",
            blockers,
        )
        raw_attachment_token = block_ids.get("archive_attachment_token")
        if not isinstance(raw_attachment_token, str) or not raw_attachment_token.strip():
            add(
                blockers,
                "delivery: readback_evidence archive_attachment_token must be a non-empty string",
            )
        else:
            evidence_archive_attachment_token = raw_attachment_token.strip()

    archive_filename: str | None = None
    archive_attachment_token: str | None = None
    archive_path: Path | None = None
    archive_sha: Any = None
    archive_size: Any = None
    archive_members: dict[str, zipfile.ZipInfo] = {}
    current_archive = receipt.get("current_archive")
    if not isinstance(current_archive, dict):
        add(blockers, "delivery: current_archive must be an object")
    else:
        archive_filename = safe_relative_path(
            current_archive.get("filename"),
            "delivery: current_archive filename",
            blockers,
        )
        if archive_filename is not None:
            if not archive_filename.lower().endswith(".zip"):
                add(blockers, "delivery: current_archive filename must end with .zip")
            if archive_filename in inventory_by_path:
                add(blockers, "delivery: current archive must not be listed in inventory")
            try:
                archive_path = asset_path(root, "", archive_filename)
            except ValueError as exc:
                add(blockers, f"delivery: current_archive {exc}")
        archive_sha = current_archive.get("sha256")
        if not isinstance(archive_sha, str) or not SHA256_RE.fullmatch(archive_sha):
            add(blockers, "delivery: current_archive sha256 must be 64 hexadecimal characters")
        archive_size = current_archive.get("size_bytes")
        if isinstance(archive_size, bool) or not isinstance(archive_size, int) or archive_size <= 0:
            add(blockers, "delivery: current_archive size_bytes must be a positive integer")
        archive_file_count = current_archive.get("file_count")
        if isinstance(archive_file_count, bool) or not isinstance(archive_file_count, int) or archive_file_count <= 0:
            add(blockers, "delivery: current_archive file_count must be a positive integer")
        attachment_token = current_archive.get("attachment_token")
        if not isinstance(attachment_token, str) or not attachment_token.strip():
            add(blockers, "delivery: current_archive attachment_token must be a non-empty string")
        else:
            archive_attachment_token = attachment_token.strip()
        cloud_size_bytes = current_archive.get("cloud_size_bytes")
        if isinstance(cloud_size_bytes, bool) or not isinstance(cloud_size_bytes, int) or cloud_size_bytes <= 0:
            add(blockers, "delivery: current_archive cloud_size_bytes must be a positive integer")
        elif isinstance(archive_size, int) and not isinstance(archive_size, bool) and cloud_size_bytes != archive_size:
            add(blockers, "delivery: current_archive cloud_size_bytes must match local size_bytes")

        if archive_path is not None:
            if archive_path == receipt_path:
                add(blockers, "delivery: current_archive cannot be the receipt")
            elif not archive_path.is_file():
                add(blockers, f"delivery: current archive file not found: {archive_path}")
            else:
                checked_files.add(archive_path)
                if isinstance(archive_sha, str) and SHA256_RE.fullmatch(archive_sha):
                    if sha256_file(archive_path).lower() != archive_sha.lower():
                        add(blockers, "delivery: current_archive sha256 mismatch")
                if isinstance(archive_size, int) and not isinstance(archive_size, bool):
                    if archive_path.stat().st_size != archive_size:
                        add(blockers, "delivery: current_archive size_bytes mismatch")
                try:
                    with zipfile.ZipFile(archive_path) as archive:
                        bad_member = archive.testzip()
                        if bad_member is not None:
                            add(blockers, f"delivery: current archive CRC failed: {bad_member}")
                        for info in archive.infolist():
                            raw_name = info.filename
                            normalized = raw_name.replace("\\", "/")
                            parts = Path(normalized).parts
                            if (
                                raw_name.startswith(("/", "\\"))
                                or re.match(r"^[A-Za-z]:", normalized)
                                or ".." in parts
                            ):
                                add(blockers, f"delivery: unsafe ZIP member path: {raw_name}")
                                continue
                            if not info.is_dir():
                                member_path = Path(normalized).as_posix()
                                if member_path in archive_members:
                                    add(blockers, f"delivery: duplicate ZIP member path: {member_path}")
                                else:
                                    archive_members[member_path] = info
                        if any(Path(name).name == DELIVERY_RECEIPT_FILENAME for name in archive_members):
                            add(blockers, f"delivery: {DELIVERY_RECEIPT_FILENAME} must not be inside the ZIP")
                        if isinstance(archive_file_count, int) and not isinstance(archive_file_count, bool):
                            if len(archive_members) != archive_file_count:
                                add(blockers, "delivery: current_archive file_count does not match ZIP members")
                        try:
                            manifest_member_path = relative_to_root(root, manifest_path)
                        except ValueError:
                            add(blockers, "delivery: production manifest must be inside the project root")
                        else:
                            manifest_info = archive_members.get(manifest_member_path)
                            if manifest_info is None:
                                add(blockers, f"delivery: ZIP missing production manifest {manifest_member_path}")
                            elif hashlib.sha256(archive.read(manifest_info)).hexdigest() != sha256_file(manifest_path):
                                add(blockers, "delivery: ZIP production manifest differs from the current local manifest")
                        for inventory_path, inventory_item in inventory_by_path.items():
                            info = archive_members.get(inventory_path)
                            if info is None:
                                add(blockers, f"delivery: ZIP missing inventory member {inventory_path}")
                                continue
                            expected_sha = inventory_item.get("sha256")
                            if isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha):
                                actual_sha = hashlib.sha256(archive.read(info)).hexdigest()
                                if actual_sha.lower() != expected_sha.lower():
                                    add(blockers, f"delivery: ZIP member sha256 mismatch: {inventory_path}")
                except (OSError, zipfile.BadZipFile) as exc:
                    add(blockers, f"delivery: current archive is not a readable ZIP: {exc}")

    if (
        evidence_archive_attachment_token is not None
        and archive_attachment_token is not None
        and evidence_archive_attachment_token != archive_attachment_token
    ):
        add(blockers, "delivery: readback_evidence archive_attachment_token does not match current_archive")

    expected_counts = expected_delivery_counts(
        segments,
        images,
        voices,
        inventory_by_path,
    )
    readback = receipt.get("readback")
    if not isinstance(readback, dict):
        add(blockers, "delivery: readback must be an object")
    else:
        for field in DELIVERY_CLASSIFIED_COUNT_FIELDS:
            actual = readback.get(field)
            if isinstance(actual, bool) or not isinstance(actual, int) or actual < 0:
                add(blockers, f"delivery: readback {field} must be a non-negative integer")
            elif actual != expected_counts[field]:
                add(blockers, f"delivery: readback {field} {actual} does not match manifest {expected_counts[field]}")
        if evidence_data is not None:
            evidence_counts = evidence_data.get("counts")
            if not isinstance(evidence_counts, dict):
                add(blockers, "delivery: readback_evidence counts must be an object")
            else:
                for field in DELIVERY_CLASSIFIED_COUNT_FIELDS:
                    evidence_count = evidence_counts.get(field)
                    if isinstance(evidence_count, bool) or not isinstance(evidence_count, int) or evidence_count < 0:
                        add(
                            blockers,
                            f"delivery: readback_evidence counts.{field} must be a non-negative integer",
                        )
                    elif evidence_count != expected_counts[field]:
                        add(
                            blockers,
                            f"delivery: readback_evidence counts.{field} {evidence_count} does not match manifest {expected_counts[field]}",
                        )
                    elif readback.get(field) != evidence_count:
                        add(
                            blockers,
                            f"delivery: readback {field} does not match readback_evidence counts",
                        )
        readback_archive_filename = readback.get("archive_filename")
        if not isinstance(readback_archive_filename, str) or not readback_archive_filename.strip():
            add(blockers, "delivery: readback archive_filename must be a non-empty string")
        elif archive_filename is None or readback_archive_filename.strip() != archive_filename:
            add(blockers, "delivery: readback archive_filename does not match current_archive filename")
        readback_attachment_token = readback.get("attachment_token")
        if not isinstance(readback_attachment_token, str) or not readback_attachment_token.strip():
            add(blockers, "delivery: readback attachment_token must be a non-empty string")
        elif not isinstance(current_archive, dict) or readback_attachment_token.strip() != str(
            current_archive.get("attachment_token", "")
        ).strip():
            add(blockers, "delivery: readback attachment_token does not match current_archive attachment_token")
        readback_cloud_size = readback.get("cloud_size_bytes")
        if isinstance(readback_cloud_size, bool) or not isinstance(readback_cloud_size, int) or readback_cloud_size <= 0:
            add(blockers, "delivery: readback cloud_size_bytes must be a positive integer")
        elif not isinstance(current_archive, dict) or readback_cloud_size != current_archive.get("cloud_size_bytes"):
            add(blockers, "delivery: readback cloud_size_bytes does not match current_archive cloud_size_bytes")

    if hardened_receipt:
        prompt_hash_profile = receipt.get("prompt_hash_profile")
        if prompt_hash_profile != "sha256:utf8-nfc-lf-strip-v1":
            add(blockers, "delivery: prompt_hash_profile must be sha256:utf8-nfc-lf-strip-v1")
        if evidence_data is not None:
            if evidence_data.get("delivery_evidence_version") != HARDENED_DELIVERY_RECEIPT_VERSION:
                add(blockers, "delivery: readback_evidence delivery_evidence_version must be 2")
            if evidence_data.get("prompt_hash_profile") != prompt_hash_profile:
                add(blockers, "delivery: readback_evidence prompt_hash_profile does not match receipt")

        previewed_voice_ids = receipt.get("previewed_voice_ids")
        ready_voice_ids = {
            str(voice_id)
            for voice_id, voice in voices.items()
            if isinstance(voice, dict) and voice.get("ready") is True
        }
        if not isinstance(previewed_voice_ids, list) or any(not isinstance(item, str) for item in previewed_voice_ids):
            add(blockers, "delivery: previewed_voice_ids must be an array of ready voice IDs")
        else:
            if len(previewed_voice_ids) != len(set(previewed_voice_ids)):
                add(blockers, "delivery: previewed_voice_ids contains duplicates")
            if set(previewed_voice_ids) != ready_voice_ids:
                add(blockers, "delivery: previewed_voice_ids must contain every ready voice ID exactly once")

        segment_readback = receipt.get("segment_readback")
        validate_segment_readback(
            segment_readback,
            segments,
            block_ids.get("segment_row_ids"),
            blockers,
        )
        voice_playback = receipt.get("voice_playback_verification")
        validate_voice_playback_verification(
            voice_playback,
            voices,
            block_ids.get("voice_block_ids"),
            blockers,
        )
        visual_preview = receipt.get("visual_preview_evidence")
        visual_filename = (
            safe_relative_path(
                visual_preview.get("filename"),
                "delivery: visual_preview_evidence filename",
                blockers,
            )
            if isinstance(visual_preview, dict)
            else None
        )
        validate_visual_preview_evidence(
            visual_preview,
            root,
            receipt_path,
            document_id,
            receipt.get("revision"),
            characters,
            scenes,
            images,
            voices,
            segments,
            blockers,
            checked_files,
        )

        if evidence_data is not None:
            for field, receipt_value in (
                ("segment_readback", segment_readback),
                ("voice_playback_verification", voice_playback),
                ("visual_preview_evidence", visual_preview),
            ):
                if evidence_data.get(field) != receipt_value:
                    add(blockers, f"delivery: readback_evidence {field} does not match receipt")

        cloud_download = current_archive.get("cloud_download") if isinstance(current_archive, dict) else None
        download_filename: str | None = None
        if not isinstance(cloud_download, dict):
            add(blockers, "delivery: current_archive.cloud_download must be an object")
        else:
            download_filename = safe_relative_path(
                cloud_download.get("filename"),
                "delivery: current_archive.cloud_download filename",
                blockers,
            )
            download_path: Path | None = None
            if download_filename is not None:
                if not download_filename.lower().endswith(".zip"):
                    add(blockers, "delivery: current_archive.cloud_download filename must end with .zip")
                try:
                    download_path = asset_path(root, "", download_filename)
                except ValueError as exc:
                    add(blockers, f"delivery: current_archive.cloud_download {exc}")
            download_sha = cloud_download.get("sha256")
            if not isinstance(download_sha, str) or not SHA256_RE.fullmatch(download_sha):
                add(blockers, "delivery: current_archive.cloud_download sha256 must be 64 hexadecimal characters")
            elif isinstance(archive_sha, str) and SHA256_RE.fullmatch(archive_sha) and download_sha.lower() != archive_sha.lower():
                add(blockers, "delivery: current_archive.cloud_download sha256 does not match local archive")
            download_size = cloud_download.get("size_bytes")
            if isinstance(download_size, bool) or not isinstance(download_size, int) or download_size <= 0:
                add(blockers, "delivery: current_archive.cloud_download size_bytes must be a positive integer")
            elif isinstance(archive_size, int) and not isinstance(archive_size, bool) and download_size != archive_size:
                add(blockers, "delivery: current_archive.cloud_download size_bytes does not match local archive")
            if not is_valid_rfc3339(cloud_download.get("downloaded_at")):
                add(blockers, "delivery: current_archive.cloud_download downloaded_at must be RFC 3339 with timezone")
            cloud_attachment_token = cloud_download.get("attachment_token")
            if not isinstance(cloud_attachment_token, str) or not cloud_attachment_token.strip():
                add(blockers, "delivery: current_archive.cloud_download attachment_token must be non-empty")
            elif archive_attachment_token is not None and cloud_attachment_token.strip() != archive_attachment_token:
                add(blockers, "delivery: current_archive.cloud_download attachment_token does not match current archive")
            if download_path is not None:
                if download_path in {receipt_path, archive_path}:
                    add(blockers, "delivery: cloud-downloaded archive must be a separate evidence file")
                elif not download_path.is_file():
                    add(blockers, f"delivery: cloud-downloaded archive file not found: {download_path}")
                else:
                    checked_files.add(download_path)
                    if isinstance(download_sha, str) and SHA256_RE.fullmatch(download_sha):
                        if sha256_file(download_path).lower() != download_sha.lower():
                            add(blockers, "delivery: current_archive.cloud_download sha256 mismatch")
                    if isinstance(download_size, int) and not isinstance(download_size, bool):
                        if download_path.stat().st_size != download_size:
                            add(blockers, "delivery: current_archive.cloud_download size_bytes mismatch")
            if evidence_data is not None and evidence_data.get("cloud_archive_download") != cloud_download:
                add(blockers, "delivery: readback_evidence cloud_archive_download does not match receipt")

        for label, package_out_filename in (
            ("readback_evidence", evidence_filename),
            ("visual_preview_evidence", visual_filename),
            ("current_archive.cloud_download", download_filename),
        ):
            if package_out_filename is None:
                continue
            if package_out_filename in inventory_by_path:
                add(blockers, f"delivery: {label} must not be listed in inventory")
            if package_out_filename in archive_members:
                add(blockers, f"delivery: {label} must not be inside the source ZIP")

    return not blockers, hardened_receipt and not blockers, receipt_version


def validate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else manifest_path.parent
    errors: list[str] = []
    image_blockers: list[str] = []
    pending_voice_blockers: list[str] = []
    voice_config_blockers: list[str] = []
    character_blockers: list[str] = []
    scene_blockers: list[str] = []
    delivery_blockers: list[str] = []
    warnings: list[str] = []
    checked_files: set[Path] = set()

    try:
        data = read_json(manifest_path)
    except ValueError as exc:
        result = {
            "passed": False,
            "ready_for_generation": False,
            "ready_for_document_delivery": False,
            "ready_for_delivery": False,
            "delivery_verified": False,
            "delivery_hardened_verified": False,
            "delivery_evidence_version": None,
            "delivery_assurance": "none",
            "delivery_verification_scope": "local_readback_evidence_only",
            "errors": [str(exc)],
            "blockers": [],
            "warnings": [],
        }
        return result, 1

    for name, value in (
        ("min-duration", args.min_duration),
        ("max-duration", args.max_duration),
        ("dialogue-rate", args.dialogue_rate),
    ):
        if not math.isfinite(value):
            add(errors, f"--{name} must be finite")
    if math.isfinite(args.min_duration) and math.isfinite(args.max_duration):
        if args.min_duration < 0 or args.max_duration < args.min_duration:
            add(errors, "duration bounds must satisfy 0 <= min-duration <= max-duration")
    if math.isfinite(args.dialogue_rate) and args.dialogue_rate <= 0:
        add(errors, "--dialogue-rate must be greater than zero")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        add(errors, "top-level title must be a non-empty string")

    raw_schema_version = data.get("schema_version", 1)
    if isinstance(raw_schema_version, bool) or not isinstance(raw_schema_version, int):
        add(errors, "schema_version must be integer 1 or 2")
        schema_version = 1
    elif raw_schema_version not in (1, 2):
        add(errors, "schema_version must be 1 or 2")
        schema_version = raw_schema_version
    else:
        schema_version = raw_schema_version

    image_dir = args.image_dir if args.image_dir is not None else ("" if schema_version == 2 else "参考图")
    voice_dir = args.voice_dir if args.voice_dir is not None else ("05_声样" if schema_version == 2 else "声样")
    prompt_dir = args.prompt_dir if args.prompt_dir is not None else ("06_逐段提示词" if schema_version == 2 else "逐段提示词")

    if "segments" not in data:
        add(errors, "top-level segments is missing")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        add(errors, "segments must be a non-empty array")
        segments = []

    if "images" not in data:
        add(errors, "top-level images is missing")
    images = data.get("images", {})
    if not isinstance(images, dict):
        add(errors, "images must be an object keyed by asset ID")
        images = {}

    if "voices" not in data:
        add(errors, "top-level voices is missing")
    voices = data.get("voices", {})
    if not isinstance(voices, dict):
        add(errors, "voices must be an object keyed by voice ID")
        voices = {}

    characters: dict[str, Any] = {}
    scenes: dict[str, Any] = {}
    requirements: dict[str, Any] = {}
    inventory_by_path: dict[str, dict[str, Any]] = {}
    inventory_role_counts: Counter[str] = Counter()
    story_contract: dict[str, Any] = {}
    contract_expected_id_sets: dict[str, set[str]] = {}
    contract_aspect_ratio: str | None = None
    if schema_version == 2:
        if "characters" not in data:
            add(errors, "schema v2 requires top-level characters")
        raw_characters = data.get("characters", {})
        if not isinstance(raw_characters, dict) or not raw_characters:
            add(errors, "characters must be a non-empty object keyed by character ID")
        else:
            characters = raw_characters

        if "scenes" not in data:
            add(errors, "schema v2 requires top-level scenes")
        raw_scenes = data.get("scenes", {})
        if not isinstance(raw_scenes, dict) or not raw_scenes:
            add(errors, "scenes must be a non-empty object keyed by scene ID")
        else:
            scenes = raw_scenes

        raw_requirements = data.get("requirements")
        if not isinstance(raw_requirements, dict):
            add(errors, "schema v2 requires top-level requirements object")
        else:
            requirements = raw_requirements

        if "delivery" in data:
            add(errors, f"schema v2 must not embed delivery; use project-root {DELIVERY_RECEIPT_FILENAME}")

        raw_required_files = data.get("required_files")
        if not isinstance(raw_required_files, list) or not raw_required_files:
            add(errors, "schema v2 requires non-empty required_files array")
            raw_required_files = []
        required_file_paths: list[str] = []
        for index, raw_path in enumerate(raw_required_files, start=1):
            normalized = safe_relative_path(raw_path, f"required_files item {index}", errors)
            if normalized is not None:
                required_file_paths.append(normalized)
        if len(required_file_paths) != len(set(required_file_paths)):
            add(errors, "required_files must not contain duplicate paths")
        if any(Path(path).name == DELIVERY_RECEIPT_FILENAME for path in required_file_paths):
            add(errors, f"required_files must not include {DELIVERY_RECEIPT_FILENAME}")

        raw_inventory = data.get("inventory")
        if not isinstance(raw_inventory, list) or not raw_inventory:
            add(errors, "schema v2 requires non-empty inventory array")
            raw_inventory = []
        for index, item in enumerate(raw_inventory, start=1):
            label = f"inventory item {index}"
            if not isinstance(item, dict):
                add(errors, f"{label} must be an object")
                continue
            path_value = safe_relative_path(item.get("path"), f"{label} path", errors)
            expected_sha = item.get("sha256")
            if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
                add(errors, f"{label} sha256 must be 64 hexadecimal characters")
            asset_role = item.get("asset_role")
            if asset_role not in INVENTORY_ASSET_ROLES:
                add(errors, f"{label} asset_role must be one of {sorted(INVENTORY_ASSET_ROLES)}")
            if asset_role in CHARACTER_INVENTORY_ROLES:
                owner_id = item.get("owner_id")
                if not isinstance(owner_id, str) or owner_id not in characters:
                    add(errors, f"{label} {asset_role} owner_id must reference one character")
                else:
                    owner = characters.get(owner_id)
                    owner_class = owner.get("character_class") if isinstance(owner, dict) else None
                    if asset_role in {"character_image", "character_image_prompt"} and owner_class not in {
                        "protagonist",
                        "antagonist",
                        "recurring_supporting",
                        "one_off_named",
                    }:
                        add(errors, f"{label} {asset_role} owner_id must be a physical character")
                    if asset_role in {"system_ui_image", "system_ui_prompt"} and not (
                        owner_class == "system_entity" and owner.get("visible_ui") is True
                    ):
                        add(errors, f"{label} {asset_role} owner_id must be a visible system_entity")
                if any(key in item for key in ("scene_id", "character_id", "segment_id")):
                    add(errors, f"{label} {asset_role} must use owner_id only")
            elif asset_role in SCENE_INVENTORY_ROLES:
                scene_id = item.get("scene_id")
                if not isinstance(scene_id, str) or scene_id not in scenes:
                    add(errors, f"{label} {asset_role} scene_id must reference one scene")
                if any(key in item for key in ("owner_id", "character_id", "segment_id")):
                    add(errors, f"{label} {asset_role} must use scene_id only")
            elif asset_role == "voice_sample":
                character_id = item.get("character_id")
                if not isinstance(character_id, str) or character_id not in characters:
                    add(errors, f"{label} voice_sample character_id must reference one character")
                if any(key in item for key in ("owner_id", "scene_id", "segment_id")):
                    add(errors, f"{label} voice_sample must use character_id only")
            elif asset_role == "segment_prompt":
                segment_id = item.get("segment_id")
                if not isinstance(segment_id, str) or segment_id not in collect_segment_ids(segments):
                    add(errors, f"{label} segment_prompt segment_id must reference one segment")
                if any(key in item for key in ("owner_id", "scene_id", "character_id")):
                    add(errors, f"{label} segment_prompt must use segment_id only")
            if path_value is None:
                continue
            if path_value in inventory_by_path:
                add(errors, f"inventory has duplicate path {path_value}")
                continue
            inventory_by_path[path_value] = item
            if isinstance(asset_role, str):
                inventory_role_counts[asset_role] += 1
            try:
                inventory_path = asset_path(root, "", path_value)
            except ValueError as exc:
                add(errors, f"{label}: {exc}")
                continue
            if inventory_path is None or not inventory_path.is_file():
                add(errors, f"{label} file not found: {inventory_path}")
                continue
            checked_files.add(inventory_path)
            if isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha):
                if sha256_file(inventory_path).lower() != expected_sha.lower():
                    add(errors, f"{label} sha256 mismatch: {path_value}")

        if set(required_file_paths) != set(inventory_by_path):
            missing_inventory = sorted(set(required_file_paths) - set(inventory_by_path))
            missing_required = sorted(set(inventory_by_path) - set(required_file_paths))
            if missing_inventory:
                add(errors, f"required_files missing inventory metadata: {missing_inventory}")
            if missing_required:
                add(errors, f"inventory paths missing from required_files: {missing_required}")

        story_contract_ref = data.get("story_contract")
        if not isinstance(story_contract_ref, dict):
            add(errors, "schema v2 requires top-level story_contract object")
        else:
            contract_filename = safe_relative_path(
                story_contract_ref.get("filename"),
                "story_contract filename",
                errors,
            )
            contract_sha = story_contract_ref.get("sha256")
            if not isinstance(contract_sha, str) or not SHA256_RE.fullmatch(contract_sha):
                add(errors, "story_contract sha256 must be 64 hexadecimal characters")
            if contract_filename is not None:
                contract_inventory_item = inventory_by_path.get(contract_filename)
                if (
                    contract_inventory_item is None
                    or contract_inventory_item.get("asset_role") != "story_contract"
                ):
                    add(errors, "story_contract filename must reference the story_contract inventory item")
                elif (
                    isinstance(contract_sha, str)
                    and SHA256_RE.fullmatch(contract_sha)
                    and contract_inventory_item.get("sha256", "").lower() != contract_sha.lower()
                ):
                    add(errors, "story_contract sha256 must match its inventory sha256")
                try:
                    contract_path = asset_path(root, "", contract_filename)
                except ValueError as exc:
                    add(errors, f"story_contract: {exc}")
                else:
                    if contract_path is None or not contract_path.is_file():
                        add(errors, f"story_contract file not found: {contract_path}")
                    else:
                        checked_files.add(contract_path)
                        if isinstance(contract_sha, str) and SHA256_RE.fullmatch(contract_sha):
                            if sha256_file(contract_path).lower() != contract_sha.lower():
                                add(errors, "story_contract sha256 mismatch")
                        try:
                            story_contract = read_json(contract_path)
                        except ValueError as exc:
                            add(errors, f"story_contract JSON is invalid: {exc}")

        if story_contract:
            contract_title = story_contract.get("title")
            if not isinstance(contract_title, str) or not contract_title.strip():
                add(errors, "story_contract title must be a non-empty string")
            elif isinstance(title, str) and contract_title.strip() != title.strip():
                add(errors, "story_contract title does not match manifest title")

            confirmed_outline = story_contract.get("confirmed_outline")
            if not isinstance(confirmed_outline, dict):
                add(errors, "story_contract confirmed_outline must be an object")
            else:
                outline_filename = safe_relative_path(
                    confirmed_outline.get("filename"),
                    "story_contract confirmed_outline filename",
                    errors,
                )
                outline_sha = confirmed_outline.get("sha256")
                if not isinstance(outline_sha, str) or not SHA256_RE.fullmatch(outline_sha):
                    add(errors, "story_contract confirmed_outline sha256 must be 64 hexadecimal characters")
                if not is_valid_rfc3339(confirmed_outline.get("confirmed_at")):
                    add(
                        errors,
                        "story_contract confirmed_outline confirmed_at must be an RFC 3339 timestamp with timezone",
                    )
                confirmation_evidence = confirmed_outline.get("evidence")
                if not isinstance(confirmation_evidence, str) or not confirmation_evidence.strip():
                    add(
                        errors,
                        "story_contract confirmed_outline evidence must be a non-empty confirmation record",
                    )
                if outline_filename is not None:
                    inventory_item = inventory_by_path.get(outline_filename)
                    if inventory_item is None or inventory_item.get("asset_role") != "confirmed_outline":
                        add(
                            errors,
                            "story_contract confirmed_outline filename must reference the confirmed_outline inventory item",
                        )
                    elif (
                        isinstance(outline_sha, str)
                        and SHA256_RE.fullmatch(outline_sha)
                        and inventory_item.get("sha256", "").lower() != outline_sha.lower()
                    ):
                        add(
                            errors,
                            "story_contract confirmed_outline sha256 must match its inventory sha256",
                        )
                    try:
                        outline_path = asset_path(root, "", outline_filename)
                    except ValueError as exc:
                        add(errors, f"story_contract confirmed_outline: {exc}")
                    else:
                        if outline_path is None or not outline_path.is_file():
                            add(errors, f"story_contract confirmed_outline file not found: {outline_path}")
                        elif isinstance(outline_sha, str) and SHA256_RE.fullmatch(outline_sha):
                            if sha256_file(outline_path).lower() != outline_sha.lower():
                                add(errors, "story_contract confirmed_outline sha256 mismatch")

                legacy_outline = data.get("confirmed_outline")
                if legacy_outline is not None and legacy_outline != confirmed_outline:
                    add(errors, "top-level confirmed_outline must exactly match story_contract confirmed_outline")

            contract_field_names = (
                "expected_character_ids",
                "expected_core_scene_ids",
                "expected_segment_ids",
            )
            for field in contract_field_names:
                normalized_ids = validate_string_id_array(
                    story_contract.get(field),
                    f"story_contract {field}",
                    errors,
                )
                contract_expected_id_sets[field] = set(normalized_ids)
            raw_contract_aspect = story_contract.get("aspect_ratio")
            if not isinstance(raw_contract_aspect, str) or not raw_contract_aspect.strip():
                add(errors, "story_contract aspect_ratio must be a non-empty string")
            else:
                contract_aspect_ratio = raw_contract_aspect.strip()
    elif args.require_feishu_delivery or args.require_hardened_feishu_delivery or args.delivery_receipt:
        add(delivery_blockers, "delivery: receipt validation requires schema_version 2")

    referenced_images = collect_references(segments, "images")
    if schema_version == 2:
        referenced_images |= collect_asset_references(characters, "reference_image_ids")
        referenced_images |= collect_asset_references(scenes, "reference_image_ids")
    referenced_voices = collect_references(segments, "voices")

    if schema_version == 2:
        actual_id_sets = {
            "expected_character_ids": {
                key for key in characters if isinstance(key, str) and key.strip()
            },
            "expected_core_scene_ids": {
                key
                for key, item in scenes.items()
                if isinstance(key, str) and isinstance(item, dict) and item.get("scene_class") == "core"
            },
            "expected_segment_ids": collect_segment_ids(segments),
        }
        expected_values = {
            "expected_character_count": len(characters),
            "expected_core_scene_count": sum(
                1 for item in scenes.values() if isinstance(item, dict) and item.get("scene_class") == "core"
            ),
            "expected_segment_count": len(segments),
        }
        contract_count_fields = {
            "expected_character_count": "expected_character_ids",
            "expected_core_scene_count": "expected_core_scene_ids",
            "expected_segment_count": "expected_segment_ids",
        }
        for field, actual in expected_values.items():
            declared = requirements.get(field)
            if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
                add(errors, f"requirements {field} must be a positive integer")
            elif declared != actual:
                add(errors, f"requirements {field} {declared} does not match actual {actual}")
            contract_count = len(contract_expected_id_sets.get(contract_count_fields[field], set()))
            if declared != contract_count:
                add(errors, f"requirements {field} does not match story_contract")
        required_aspect_ratio = requirements.get("aspect_ratio")
        if not isinstance(required_aspect_ratio, str) or not required_aspect_ratio.strip():
            add(errors, "requirements aspect_ratio must be a non-empty string")
        elif contract_aspect_ratio is not None and required_aspect_ratio.strip() != contract_aspect_ratio:
            add(errors, "requirements aspect_ratio does not match story_contract")
        for field, actual_ids in actual_id_sets.items():
            normalized_ids = validate_string_id_array(
                requirements.get(field),
                f"requirements {field}",
                errors,
            )
            declared_id_set = set(normalized_ids)
            if declared_id_set != actual_ids:
                add(errors, f"requirements {field} does not exactly match actual IDs")
            if declared_id_set != contract_expected_id_sets.get(field, set()):
                add(errors, f"requirements {field} does not exactly match story_contract")
            if contract_expected_id_sets.get(field, set()) != actual_ids:
                add(errors, f"story_contract {field} does not exactly match actual IDs")
        for role in sorted(GLOBAL_REQUIRED_ROLES):
            if inventory_role_counts[role] < 1:
                add(errors, f"inventory requires at least one {role} file")
        if inventory_role_counts["story_contract"] != 1:
            add(errors, "inventory requires exactly one story_contract file")

    image_content_hashes: dict[str, str] = {}
    image_paths: dict[str, Path] = {}
    exclusive_image_hash_owners: dict[str, str] = {}
    for image_id, item in images.items():
        label = f"image {image_id}"
        if schema_version == 2 and (not isinstance(image_id, str) or not image_id.strip()):
            add(errors, "image IDs must be non-empty strings in schema v2")
        if not isinstance(item, dict):
            add(errors, f"{label}: entry must be an object")
            continue
        asset_type = item.get("asset_type")
        if schema_version == 2:
            if asset_type not in IMAGE_ASSET_TYPES:
                add(errors, f"{label}: asset_type must be one of {sorted(IMAGE_ASSET_TYPES)}")
            for field in ("purpose", "prohibited_use", "source"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    add(errors, f"{label}: {field} must be a non-empty string")
            if item.get("qc_status") != "passed":
                add(errors, f"{label}: qc_status must be passed")

            if asset_type in {"character_turnaround", "character_identity", "system_ui_anchor"}:
                owner_id = item.get("owner_id")
                if not isinstance(owner_id, str) or owner_id not in characters:
                    add(errors, f"{label}: owner_id must reference one character")
                if "character_ids" in item or "scene_id" in item:
                    add(errors, f"{label}: exclusive character assets use owner_id only")
                owner = characters.get(owner_id) if isinstance(owner_id, str) else None
                owner_class = owner.get("character_class") if isinstance(owner, dict) else None
                if asset_type == "character_turnaround" and owner_class not in {
                    "protagonist", "antagonist", "recurring_supporting"
                }:
                    add(errors, f"{label}: character_turnaround owner must be a physical recurring character")
                if asset_type == "character_identity" and owner_class != "one_off_named":
                    add(errors, f"{label}: character_identity owner must be one_off_named")
                if asset_type == "system_ui_anchor" and owner_class != "system_entity":
                    add(errors, f"{label}: system_ui_anchor owner must be system_entity")
            elif asset_type in {"scene_anchor", "scene_variant"}:
                scene_id = item.get("scene_id")
                if not isinstance(scene_id, str) or scene_id not in scenes:
                    add(errors, f"{label}: scene_id must reference one scene")
                if "owner_id" in item or "character_ids" in item:
                    add(errors, f"{label}: scene assets use scene_id only")
                scene = scenes.get(scene_id) if isinstance(scene_id, str) else None
                if asset_type == "scene_anchor" and (
                    not isinstance(scene, dict) or scene.get("scene_class") != "core"
                ):
                    add(errors, f"{label}: scene_anchor must belong to a core scene")
            elif asset_type == "storyboard":
                character_ids = item.get("character_ids")
                scene_id = item.get("scene_id")
                if not isinstance(character_ids, list) or not character_ids:
                    add(errors, f"{label}: storyboard character_ids must be a non-empty array")
                else:
                    string_character_ids = [value for value in character_ids if isinstance(value, str)]
                    if len(string_character_ids) != len(set(string_character_ids)):
                        add(errors, f"{label}: storyboard character_ids contains duplicates")
                    for character_id in character_ids:
                        if not isinstance(character_id, str) or character_id not in characters:
                            add(errors, f"{label}: storyboard has unknown character_id {character_id}")
                if not isinstance(scene_id, str) or scene_id not in scenes:
                    add(errors, f"{label}: storyboard scene_id must reference one scene")
                if "owner_id" in item:
                    add(errors, f"{label}: storyboard uses character_ids and scene_id, not owner_id")
            elif asset_type == "prop_reference":
                owner_id = item.get("owner_id")
                if not isinstance(owner_id, str) or not owner_id.strip():
                    add(errors, f"{label}: prop_reference owner_id must be a stable prop ID")
        ready_value = item.get("ready")
        if not isinstance(ready_value, bool):
            add(errors, f"{label}: ready must be boolean")
        ready = ready_value is True
        if not ready:
            if schema_version == 2 or image_id in referenced_images:
                add(image_blockers, f"{label}: referenced but marked not ready")
            else:
                add(warnings, f"{label}: unreferenced asset is marked not ready")
            continue
        try:
            path = asset_path(root, image_dir, item.get("filename"))
        except ValueError as exc:
            add(errors, f"{label}: {exc}")
            continue
        if path is None:
            add(errors, f"{label}: ready but filename is missing")
        elif not path.is_file():
            add(errors, f"{label}: file not found: {path}")
        else:
            checked_files.add(path)
            image_paths[str(image_id)] = path
            validate_hash(path, item.get("sha256"), label, errors)
            if schema_version == 2:
                magic_error = image_magic_error(path)
                if magic_error is not None:
                    add(errors, f"{label}: {magic_error}")
                actual_hash = sha256_file(path)
                image_content_hashes[str(image_id)] = actual_hash
                inventory_path = relative_to_root(root, path)
                expected_role = {
                    "character_turnaround": "character_image",
                    "character_identity": "character_image",
                    "system_ui_anchor": "system_ui_image",
                    "scene_anchor": "scene_image",
                    "scene_variant": "scene_image",
                    "storyboard": "storyboard_image",
                    "prop_reference": "prop_image",
                }.get(asset_type)
                inventory_item = inventory_by_path.get(inventory_path)
                if inventory_item is None:
                    add(errors, f"{label}: file is missing from inventory: {inventory_path}")
                elif expected_role is not None and inventory_item.get("asset_role") != expected_role:
                    add(errors, f"{label}: inventory asset_role must be {expected_role}")
                elif asset_type in {"character_turnaround", "character_identity"}:
                    if inventory_item.get("owner_id") != item.get("owner_id"):
                        add(errors, f"{label}: inventory owner_id does not match image owner_id")
                elif asset_type == "system_ui_anchor":
                    if inventory_item.get("owner_id") != item.get("owner_id"):
                        add(errors, f"{label}: inventory owner_id does not match system UI owner_id")
                elif asset_type in {"scene_anchor", "scene_variant"}:
                    if inventory_item.get("scene_id") != item.get("scene_id"):
                        add(errors, f"{label}: inventory scene_id does not match image scene_id")
                if asset_type in {
                    "character_turnaround", "character_identity", "system_ui_anchor", "scene_anchor"
                }:
                    if asset_type == "scene_anchor":
                        ownership_key = f"scene:{item.get('scene_id')}"
                    else:
                        ownership_key = f"character:{item.get('owner_id')}"
                    prior_owner = exclusive_image_hash_owners.get(actual_hash)
                    if prior_owner is not None and prior_owner != ownership_key:
                        add(errors, f"{label}: identical image content is already assigned to {prior_owner}")
                    else:
                        exclusive_image_hash_owners[actual_hash] = ownership_key
            expected_size = item.get("size_bytes")
            if expected_size is not None:
                if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
                    add(errors, f"{label}: size_bytes must be a non-negative integer")
                else:
                    try:
                        actual_size = path.stat().st_size
                    except OSError as exc:
                        add(errors, f"{label}: cannot read file size: {exc}")
                    else:
                        if actual_size != expected_size:
                            add(errors, f"{label}: size mismatch: {path.name}")

    if schema_version == 2:
        for character_id, item in characters.items():
            label = f"character {character_id}"
            if not isinstance(character_id, str) or not character_id.strip():
                add(errors, "character IDs must be non-empty strings")
                continue
            if not isinstance(item, dict):
                add(errors, f"{label}: entry must be an object")
                continue
            for field in ("name", "narrative_role", "profile", "appearance"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    add(errors, f"{label}: {field} must be a non-empty string")
            character_class = item.get("character_class")
            if character_class not in CHARACTER_CLASSES:
                add(errors, f"{label}: character_class must be one of {sorted(CHARACTER_CLASSES)}")
            visible_ui = False
            if character_class == "system_entity":
                visible_ui_value = item.get("visible_ui")
                if not isinstance(visible_ui_value, bool):
                    add(errors, f"{label}: system_entity requires boolean visible_ui")
                else:
                    visible_ui = visible_ui_value
            ready_value = item.get("ready")
            if not isinstance(ready_value, bool):
                add(errors, f"{label}: ready must be boolean")
            ready = ready_value is True
            reference_ids = item.get("reference_image_ids")
            require_reference_image = character_class != "system_entity" or visible_ui
            if not isinstance(reference_ids, list) or (require_reference_image and not reference_ids):
                qualifier = "non-empty " if require_reference_image else ""
                add(errors, f"{label}: reference_image_ids must be a {qualifier}array")
                reference_ids = []
            for image_id in reference_ids:
                if not isinstance(image_id, str):
                    add(errors, f"{label}: reference_image_ids must contain strings")
                elif image_id not in images:
                    add(errors, f"{label}: unknown image reference {image_id}")
                elif not isinstance(images[image_id], dict):
                    add(errors, f"{label}: image {image_id} has an invalid entry")
                elif images[image_id].get("ready") is not True:
                    add(character_blockers, f"{label}: reference image {image_id} is pending")
            required_asset_type = {
                "protagonist": "character_turnaround",
                "antagonist": "character_turnaround",
                "recurring_supporting": "character_turnaround",
                "one_off_named": "character_identity",
                "system_entity": "system_ui_anchor" if visible_ui else None,
            }.get(character_class)
            if required_asset_type is not None:
                owned_assets = {
                    image_id
                    for image_id, image in images.items()
                    if isinstance(image, dict)
                    and image.get("asset_type") == required_asset_type
                    and image.get("owner_id") == character_id
                }
                if not owned_assets:
                    add(character_blockers, f"{label}: missing dedicated {required_asset_type}")
                elif not owned_assets.intersection(reference_ids):
                    add(character_blockers, f"{label}: reference_image_ids must include its dedicated {required_asset_type}")
            if not ready:
                add(character_blockers, f"{label}: marked not ready")

        for scene_id, item in scenes.items():
            label = f"scene {scene_id}"
            if not isinstance(scene_id, str) or not scene_id.strip():
                add(errors, "scene IDs must be non-empty strings")
                continue
            if not isinstance(item, dict):
                add(errors, f"{label}: entry must be an object")
                continue
            for field in ("name", "description"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    add(errors, f"{label}: {field} must be a non-empty string")
            scene_class = item.get("scene_class")
            if scene_class not in SCENE_CLASSES:
                add(errors, f"{label}: scene_class must be one of {sorted(SCENE_CLASSES)}")
            ready_value = item.get("ready")
            if not isinstance(ready_value, bool):
                add(errors, f"{label}: ready must be boolean")
            ready = ready_value is True
            reference_ids = item.get("reference_image_ids")
            if not isinstance(reference_ids, list) or not reference_ids:
                add(errors, f"{label}: reference_image_ids must be a non-empty array")
                reference_ids = []
            for image_id in reference_ids:
                if not isinstance(image_id, str):
                    add(errors, f"{label}: reference_image_ids must contain strings")
                elif image_id not in images:
                    add(errors, f"{label}: unknown image reference {image_id}")
                elif not isinstance(images[image_id], dict):
                    add(errors, f"{label}: image {image_id} has an invalid entry")
                elif images[image_id].get("ready") is not True:
                    add(scene_blockers, f"{label}: reference image {image_id} is pending")
            if scene_class == "core":
                owned_anchors = {
                    image_id
                    for image_id, image in images.items()
                    if isinstance(image, dict)
                    and image.get("asset_type") == "scene_anchor"
                    and image.get("scene_id") == scene_id
                }
                if not owned_anchors:
                    add(scene_blockers, f"{label}: missing dedicated scene_anchor")
                elif not owned_anchors.intersection(reference_ids):
                    add(scene_blockers, f"{label}: reference_image_ids must include its dedicated scene_anchor")
            elif scene_class in {"minor", "variant"}:
                owned_variants = {
                    image_id
                    for image_id, image in images.items()
                    if isinstance(image, dict)
                    and image.get("asset_type") == "scene_variant"
                    and image.get("scene_id") == scene_id
                }
                if not owned_variants:
                    add(scene_blockers, f"{label}: missing dedicated scene_variant")
                elif not owned_variants.intersection(reference_ids):
                    add(scene_blockers, f"{label}: reference_image_ids must include its dedicated scene_variant")
            if not ready:
                add(scene_blockers, f"{label}: marked not ready")

        inventory_owner_counts: Counter[tuple[str, str]] = Counter()
        for inventory_item in inventory_by_path.values():
            if not isinstance(inventory_item, dict):
                continue
            role = inventory_item.get("asset_role")
            if role in CHARACTER_INVENTORY_ROLES and isinstance(inventory_item.get("owner_id"), str):
                inventory_owner_counts[(str(role), inventory_item["owner_id"])] += 1
            elif role in SCENE_INVENTORY_ROLES and isinstance(inventory_item.get("scene_id"), str):
                inventory_owner_counts[(str(role), inventory_item["scene_id"])] += 1

        for character_id, character in characters.items():
            if not isinstance(character_id, str) or not isinstance(character, dict):
                continue
            if inventory_owner_counts[("character_card", character_id)] != 1:
                add(errors, f"character {character_id}: inventory requires exactly one owned character_card")
            character_class = character.get("character_class")
            if character_class in {
                "protagonist",
                "antagonist",
                "recurring_supporting",
                "one_off_named",
            } and inventory_owner_counts[("character_image_prompt", character_id)] != 1:
                add(
                    errors,
                    f"character {character_id}: inventory requires exactly one owned character_image_prompt",
                )
            if (
                character_class == "system_entity"
                and character.get("visible_ui") is True
                and inventory_owner_counts[("system_ui_prompt", character_id)] != 1
            ):
                add(errors, f"character {character_id}: inventory requires exactly one owned system_ui_prompt")

        for scene_id, scene in scenes.items():
            if not isinstance(scene_id, str) or not isinstance(scene, dict):
                continue
            if inventory_owner_counts[("scene_card", scene_id)] != 1:
                add(errors, f"scene {scene_id}: inventory requires exactly one owned scene_card")
            if inventory_owner_counts[("scene_image_prompt", scene_id)] != 1:
                add(errors, f"scene {scene_id}: inventory requires exactly one owned scene_image_prompt")

        images_by_inventory_path: dict[str, list[dict[str, Any]]] = {}
        for image_id, path in image_paths.items():
            image = images.get(image_id)
            if isinstance(image, dict):
                images_by_inventory_path.setdefault(relative_to_root(root, path), []).append(image)
        inventory_image_types = {
            "character_image": {"character_turnaround", "character_identity"},
            "system_ui_image": {"system_ui_anchor"},
            "scene_image": {"scene_anchor", "scene_variant"},
        }
        for inventory_path, inventory_item in inventory_by_path.items():
            role = inventory_item.get("asset_role")
            if role not in inventory_image_types:
                continue
            candidates = images_by_inventory_path.get(inventory_path, [])
            if role in {"character_image", "system_ui_image"}:
                binding_matches = [
                    image
                    for image in candidates
                    if image.get("asset_type") in inventory_image_types[role]
                    and image.get("owner_id") == inventory_item.get("owner_id")
                ]
            else:
                binding_matches = [
                    image
                    for image in candidates
                    if image.get("asset_type") in inventory_image_types[role]
                    and image.get("scene_id") == inventory_item.get("scene_id")
                ]
            if not binding_matches:
                add(errors, f"inventory {inventory_path}: {role} does not match any manifest image path and owner")

    voice_content_hashes: dict[str, str] = {}
    voice_paths: dict[str, Path] = {}
    for voice_id, item in voices.items():
        label = f"voice {voice_id}"
        if schema_version == 2 and (not isinstance(voice_id, str) or not voice_id.strip()):
            add(errors, "voice IDs must be non-empty strings in schema v2")
        if not isinstance(item, dict):
            add(errors, f"{label}: entry must be an object")
            continue
        character_id = item.get("character_id")
        ready_value = item.get("ready")
        if not isinstance(ready_value, bool):
            add(errors, f"{label}: ready must be boolean")
        ready = ready_value is True
        if schema_version == 2:
            if not isinstance(character_id, str) or not character_id.strip():
                add(errors, f"{label}: schema v2 requires character_id")
            elif character_id not in characters:
                add(errors, f"{label}: unknown character_id {character_id}")
            if ready:
                source = item.get("source")
                if not isinstance(source, str) or not source.strip():
                    add(errors, f"{label}: source must be a non-empty string")
                if item.get("qc_status") != "passed":
                    add(errors, f"{label}: ready voice qc_status must be passed")
            else:
                pending_reason = item.get("pending_reason")
                if not isinstance(pending_reason, str) or not pending_reason.strip():
                    add(errors, f"{label}: pending voice requires pending_reason")
                if any(field in item for field in ("filename", "sha256", "size_bytes")):
                    add(errors, f"{label}: pending voice must not contain filename, sha256, or size_bytes")
                if item.get("qc_status") not in (None, "pending"):
                    add(errors, f"{label}: pending voice qc_status must be pending when supplied")
                if "source" in item and (
                    not isinstance(item.get("source"), str) or not item.get("source", "").strip()
                ):
                    add(errors, f"{label}: source must be a non-empty string when supplied")
        elif character_id is not None and (not isinstance(character_id, str) or not character_id.strip()):
            add(errors, f"{label}: character_id must be a non-empty string when supplied")
        if not ready:
            role = item.get("role", "unknown role")
            if schema_version == 2 or voice_id in referenced_voices:
                add(pending_voice_blockers, f"{label} ({role}): referenced but pending")
            else:
                add(warnings, f"{label} ({role}): unreferenced voice is pending")
            continue
        try:
            path = asset_path(root, voice_dir, item.get("filename"))
        except ValueError as exc:
            add(errors, f"{label}: {exc}")
            continue
        if path is None:
            add(errors, f"{label}: ready but filename is missing")
        elif not path.is_file():
            add(errors, f"{label}: file not found: {path}")
        else:
            checked_files.add(path)
            validate_hash(path, item.get("sha256"), label, errors)
            if schema_version == 2:
                magic_error = audio_magic_error(path)
                if magic_error is not None:
                    add(errors, f"{label}: {magic_error}")
                actual_hash = sha256_file(path)
                voice_content_hashes[str(voice_id)] = actual_hash
                voice_paths[str(voice_id)] = path.resolve()
                inventory_path = relative_to_root(root, path)
                inventory_item = inventory_by_path.get(inventory_path)
                if inventory_item is None:
                    add(errors, f"{label}: file is missing from inventory: {inventory_path}")
                elif inventory_item.get("asset_role") != "voice_sample":
                    add(errors, f"{label}: inventory asset_role must be voice_sample")
                elif inventory_item.get("character_id") != character_id:
                    add(errors, f"{label}: inventory character_id does not match voice character_id")

    if schema_version == 2:
        path_characters: dict[Path, set[str]] = {}
        hash_characters: dict[str, set[str]] = {}
        for voice_id, path in voice_paths.items():
            voice = voices.get(voice_id)
            character_id = voice.get("character_id") if isinstance(voice, dict) else None
            if isinstance(character_id, str) and character_id.strip():
                path_characters.setdefault(path, set()).add(character_id.strip())
        for voice_id, content_hash in voice_content_hashes.items():
            voice = voices.get(voice_id)
            character_id = voice.get("character_id") if isinstance(voice, dict) else None
            if isinstance(character_id, str) and character_id.strip():
                hash_characters.setdefault(content_hash, set()).add(character_id.strip())
        for path, character_ids in path_characters.items():
            if len(character_ids) > 1:
                add(
                    voice_config_blockers,
                    f"voice file {relative_to_root(root, path)} is shared across characters {sorted(character_ids)}",
                )
        for content_hash, character_ids in hash_characters.items():
            if len(character_ids) > 1:
                add(
                    voice_config_blockers,
                    f"voice content hash {content_hash} is shared across characters {sorted(character_ids)}",
                )

        voices_by_inventory_path: dict[str, list[dict[str, Any]]] = {}
        for voice_id, path in voice_paths.items():
            voice = voices.get(voice_id)
            if isinstance(voice, dict):
                voices_by_inventory_path.setdefault(relative_to_root(root, path), []).append(voice)
        for inventory_path, inventory_item in inventory_by_path.items():
            if inventory_item.get("asset_role") != "voice_sample":
                continue
            matching_voices = [
                voice
                for voice in voices_by_inventory_path.get(inventory_path, [])
                if voice.get("ready") is True
                and voice.get("character_id") == inventory_item.get("character_id")
            ]
            if not matching_voices:
                add(
                    errors,
                    f"inventory {inventory_path}: voice_sample does not match any ready manifest voice path and character_id",
                )

        system_character_ids = {
            character_id
            for character_id, item in characters.items()
            if isinstance(item, dict) and item.get("character_class") == "system_entity"
        }
        for system_character_id in sorted(system_character_ids):
            system_voice_ids = [
                voice_id
                for voice_id, voice in voices.items()
                if isinstance(voice, dict) and voice.get("character_id") == system_character_id
            ]
            if not system_voice_ids:
                add(voice_config_blockers, f"system_entity {system_character_id} requires an independent voice sample")
                continue
            system_hashes = {
                voice_content_hashes[voice_id]
                for voice_id in system_voice_ids
                if voice_id in voice_content_hashes
            }
            other_hashes = {
                voice_content_hashes[voice_id]
                for voice_id, voice in voices.items()
                if voice_id in voice_content_hashes
                and isinstance(voice, dict)
                and voice.get("character_id") != system_character_id
            }
            if system_hashes.intersection(other_hashes):
                add(voice_config_blockers, f"system_entity {system_character_id} voice sample is shared with another character")

        physical_character_count = sum(
            1
            for item in characters.values()
            if isinstance(item, dict)
            and item.get("character_class") in {
                "protagonist", "antagonist", "recurring_supporting", "one_off_named"
            }
        )
        visible_system_count = sum(
            1
            for item in characters.values()
            if isinstance(item, dict)
            and item.get("character_class") == "system_entity"
            and item.get("visible_ui") is True
        )
        required_role_counts = {
            "character_card": len(characters),
            "character_image": physical_character_count,
            "character_image_prompt": physical_character_count,
            "system_ui_image": visible_system_count,
            "system_ui_prompt": visible_system_count,
            "scene_card": len(scenes),
            "scene_image": len(scenes),
            "scene_image_prompt": len(scenes),
            "voice_sample": sum(
                1 for voice in voices.values() if isinstance(voice, dict) and voice.get("ready") is True
            ),
            "segment_prompt": len(segments),
        }
        for role, expected_count in required_role_counts.items():
            if inventory_role_counts[role] < expected_count:
                add(errors, f"inventory requires at least {expected_count} {role} files")

    ids: set[str] = set()
    segment_prompt_paths: dict[str, Path] = {}
    durations: list[float] = []
    aspect_ratios: set[str] = set()
    required_text = ("title", "start", "visual", "end", "change", "sound", "prompt", "prompt_filename")

    for index, segment in enumerate(segments, start=1):
        label = f"segment {index}"
        if not isinstance(segment, dict):
            add(errors, f"{label}: must be an object")
            continue

        raw_id = segment.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)) or not str(raw_id).strip():
            add(errors, f"{label}: id must be a non-empty string or integer")
            segment_id = str(index)
        else:
            segment_id = str(raw_id).strip()
            label = f"segment {segment_id}"
            if schema_version == 2 and not isinstance(raw_id, str):
                add(errors, f"{label}: id must be a string in schema v2")
        if segment_id in ids:
            add(errors, f"{label}: duplicate id")
        ids.add(segment_id)

        for field in required_text:
            value = segment.get(field)
            if not isinstance(value, str) or not value.strip():
                add(errors, f"{label}: {field} must be a non-empty string")

        duration = segment.get("duration_seconds")
        valid_duration = (
            not isinstance(duration, bool)
            and isinstance(duration, (int, float))
            and math.isfinite(float(duration))
        )
        if not valid_duration:
            add(errors, f"{label}: duration_seconds must be a finite number")
        else:
            duration_value = float(duration)
            durations.append(duration_value)
            if duration_value < args.min_duration or duration_value > args.max_duration:
                add(errors, f"{label}: duration {duration_value:g}s outside {args.min_duration:g}-{args.max_duration:g}s")

        ratio = segment.get("aspect_ratio")
        if not isinstance(ratio, str) or not ratio.strip():
            add(errors, f"{label}: aspect_ratio must be a non-empty string")
        else:
            ratio = ratio.strip()
            aspect_ratios.add(ratio)
            if args.aspect and ratio != args.aspect:
                add(errors, f"{label}: aspect_ratio {ratio} does not match required {args.aspect}")

        segment_character_ids: list[str] = []
        segment_scene_id: str | None = None
        if schema_version == 2:
            raw_character_ids = segment.get("character_ids")
            if not isinstance(raw_character_ids, list) or not raw_character_ids:
                add(errors, f"{label}: character_ids must be a non-empty array in schema v2")
            else:
                seen_character_ids: set[str] = set()
                for character_id in raw_character_ids:
                    if not isinstance(character_id, str) or not character_id.strip():
                        add(errors, f"{label}: character_ids must contain non-empty strings")
                        continue
                    character_id = character_id.strip()
                    if character_id in seen_character_ids:
                        add(errors, f"{label}: duplicate character reference {character_id}")
                        continue
                    seen_character_ids.add(character_id)
                    segment_character_ids.append(character_id)
                    if character_id not in characters:
                        add(errors, f"{label}: unknown character reference {character_id}")
                    elif not isinstance(characters[character_id], dict):
                        add(errors, f"{label}: character {character_id} has an invalid entry")
                    elif characters[character_id].get("ready") is not True:
                        add(character_blockers, f"{label}: character {character_id} is pending")

            scene_id = segment.get("scene_id")
            if not isinstance(scene_id, str) or not scene_id.strip():
                add(errors, f"{label}: scene_id must be a non-empty string in schema v2")
            else:
                segment_scene_id = scene_id.strip()
                if segment_scene_id not in scenes:
                    add(errors, f"{label}: unknown scene reference {segment_scene_id}")
                elif not isinstance(scenes[segment_scene_id], dict):
                    add(errors, f"{label}: scene {segment_scene_id} has an invalid entry")
                elif scenes[segment_scene_id].get("ready") is not True:
                    add(scene_blockers, f"{label}: scene {segment_scene_id} is pending")

            required_ratio = requirements.get("aspect_ratio")
            if isinstance(ratio, str) and isinstance(required_ratio, str) and ratio != required_ratio.strip():
                add(errors, f"{label}: aspect_ratio {ratio} does not match requirements {required_ratio.strip()}")

        segment_images = segment.get("images")
        if not isinstance(segment_images, list) or (schema_version == 2 and not segment_images):
            qualifier = "non-empty " if schema_version == 2 else ""
            add(errors, f"{label}: images must be a {qualifier}array")
            segment_images = []
        elif schema_version == 2 and len(segment_images) != len(set(map(str, segment_images))):
            add(errors, f"{label}: images must not contain duplicate asset IDs")
        for image_id in segment_images:
            if not isinstance(image_id, str):
                add(errors, f"{label}: image references must be strings")
            elif image_id not in images:
                add(errors, f"{label}: unknown image reference {image_id}")
            elif not isinstance(images[image_id], dict):
                add(errors, f"{label}: image {image_id} has an invalid entry")
            elif images[image_id].get("ready") is not True:
                add(image_blockers, f"{label}: image {image_id} is pending")

        if schema_version == 2:
            covered_character_ids: set[str] = set()
            covered_scene_ids: set[str] = set()
            for image_id in segment_images:
                image = images.get(image_id) if isinstance(image_id, str) else None
                if not isinstance(image, dict):
                    continue
                asset_type = image.get("asset_type")
                if asset_type in {"character_turnaround", "character_identity", "system_ui_anchor"}:
                    owner_id = image.get("owner_id")
                    if isinstance(owner_id, str):
                        covered_character_ids.add(owner_id)
                elif asset_type == "storyboard":
                    values = image.get("character_ids")
                    if isinstance(values, list):
                        covered_character_ids.update(value for value in values if isinstance(value, str))
                if asset_type in {"scene_anchor", "scene_variant", "storyboard"}:
                    image_scene_id = image.get("scene_id")
                    if isinstance(image_scene_id, str):
                        covered_scene_ids.add(image_scene_id)
            for character_id in segment_character_ids:
                character = characters.get(character_id)
                exempt = (
                    isinstance(character, dict)
                    and character.get("character_class") == "system_entity"
                    and character.get("visible_ui") is False
                )
                if not exempt and character_id not in covered_character_ids:
                    add(errors, f"{label}: images do not cover character {character_id}")
            if segment_scene_id is not None and segment_scene_id not in covered_scene_ids:
                add(errors, f"{label}: images do not cover scene {segment_scene_id}")

        segment_voices = segment.get("voices")
        if not isinstance(segment_voices, list):
            add(errors, f"{label}: voices must be an array")
            segment_voices = []
        elif schema_version == 2 and len(segment_voices) != len(set(map(str, segment_voices))):
            add(errors, f"{label}: voices must not contain duplicate asset IDs")
        for voice_id in segment_voices:
            if not isinstance(voice_id, str):
                add(errors, f"{label}: voice references must be strings")
            elif voice_id not in voices:
                add(errors, f"{label}: unknown voice reference {voice_id}")
            elif not isinstance(voices[voice_id], dict):
                add(errors, f"{label}: voice {voice_id} has an invalid entry")
            else:
                voice_character_id = voices[voice_id].get("character_id")
                if (
                    schema_version == 2
                    and isinstance(voice_character_id, str)
                    and voice_character_id.strip()
                    and voice_character_id.strip() not in segment_character_ids
                ):
                    add(errors, f"{label}: voice {voice_id} character_id {voice_character_id.strip()} is not in segment character_ids")
                if voices[voice_id].get("ready") is not True:
                    add(pending_voice_blockers, f"{label}: voice {voice_id} is pending")

        lines = segment.get("lines")
        if not isinstance(lines, list):
            add(errors, f"{label}: lines must be an array")
            lines = []
        dialogue = ""
        speaking_roles: set[str] = set()
        speaking_character_ids: set[str] = set()
        for line_index, line in enumerate(lines, start=1):
            if not isinstance(line, dict):
                add(errors, f"{label}: line {line_index} must be an object")
                continue
            for field in ("text", "emotion"):
                value = line.get(field)
                if not isinstance(value, str) or not value.strip():
                    add(errors, f"{label}: line {line_index} {field} must be a non-empty string")
            role = line.get("role")
            character_id = line.get("character_id")
            if schema_version == 2 and character_id is None:
                add(errors, f"{label}: line {line_index} requires character_id in schema v2")
            if character_id is not None:
                if not isinstance(character_id, str) or not character_id.strip():
                    add(errors, f"{label}: line {line_index} character_id must be a non-empty string")
                else:
                    character_id = character_id.strip()
                    speaking_character_ids.add(character_id)
                    if schema_version == 2:
                        if character_id not in characters:
                            add(errors, f"{label}: line {line_index} unknown character_id {character_id}")
                        elif character_id not in segment_character_ids:
                            add(errors, f"{label}: line {line_index} character_id {character_id} is not in segment character_ids")
            if not isinstance(role, str) or not role.strip():
                if character_id is None:
                    add(errors, f"{label}: line {line_index} requires role or character_id")
            text_value = line.get("text")
            if isinstance(role, str) and role.strip() and character_id is None:
                speaking_roles.add(role.strip())
            if isinstance(text_value, str):
                dialogue += text_value

        supplied_roles: set[str] = set()
        supplied_character_ids: set[str] = set()
        for voice_id in segment_voices:
            if not isinstance(voice_id, str):
                continue
            voice = voices.get(voice_id)
            if isinstance(voice, dict) and isinstance(voice.get("role"), str):
                supplied_roles.add(voice["role"].strip())
            if isinstance(voice, dict) and isinstance(voice.get("character_id"), str):
                supplied_character_ids.add(voice["character_id"].strip())
        for role in sorted(speaking_roles - supplied_roles):
            add(voice_config_blockers, f"{label}: speaking role {role} has no matching voice reference")
        for character_id in sorted(speaking_character_ids - supplied_character_ids):
            add(voice_config_blockers, f"{label}: speaking character {character_id} has no matching voice reference")
        if not speaking_roles and not speaking_character_ids and segment_voices:
            add(warnings, f"{label}: voice references are present but lines is empty")

        chinese_chars = len(CJK_RE.findall(dialogue))
        declared = segment.get("dialogue_chinese_characters")
        if declared is not None:
            if isinstance(declared, bool) or not isinstance(declared, int) or declared < 0:
                add(errors, f"{label}: dialogue_chinese_characters must be a non-negative integer")
            elif declared != chinese_chars:
                add(warnings, f"{label}: declared dialogue count {declared}, actual CJK count {chinese_chars}")
        if valid_duration and duration_value > 0 and chinese_chars / duration_value > args.dialogue_rate:
            add(warnings, f"{label}: dialogue density {chinese_chars / duration_value:.2f} CJK chars/s exceeds {args.dialogue_rate:.2f}")

        prompt = segment.get("prompt")
        if isinstance(prompt, str):
            if (schema_version == 2 or not args.allow_background_music) and not any(
                marker in prompt.lower() for marker in NO_BGM_MARKERS
            ):
                add(errors, f"{label}: prompt lacks an explicit no-background-music instruction")
            if schema_version == 2:
                if valid_duration and not duration_marker_present(prompt, duration_value):
                    add(errors, f"{label}: prompt must include its {duration_value:g}秒 duration")
                if isinstance(ratio, str) and ratio not in prompt:
                    add(errors, f"{label}: prompt must include aspect ratio {ratio}")
                image_prompt_numbers = {
                    int(value) for value in re.findall(r"@图片(\d+)", prompt)
                }
                expected_image_numbers = set(range(1, len(segment_images) + 1))
                if image_prompt_numbers != expected_image_numbers:
                    add(
                        errors,
                        f"{label}: prompt image references {sorted(image_prompt_numbers)} must exactly match continuous @图片1..{len(segment_images)}",
                    )
                audio_prompt_numbers = {
                    int(value) for value in re.findall(r"@音频(\d+)", prompt)
                }
                expected_audio_numbers = set(range(1, len(segment_voices) + 1))
                if audio_prompt_numbers != expected_audio_numbers:
                    add(
                        errors,
                        f"{label}: prompt audio references {sorted(audio_prompt_numbers)} must exactly match continuous @音频1..{len(segment_voices)}",
                    )
            if isinstance(segment.get("start"), str) and isinstance(segment.get("end"), str):
                if segment["start"].strip() == segment["end"].strip():
                    add(warnings, f"{label}: start and end states are identical")

        prompt_filename = segment.get("prompt_filename")
        try:
            prompt_path = asset_path(root, prompt_dir, prompt_filename)
        except ValueError as exc:
            add(errors, f"{label}: {exc}")
            prompt_path = None
        if prompt_path is not None:
            if not prompt_path.is_file():
                add(errors, f"{label}: prompt file not found: {prompt_path}")
            else:
                checked_files.add(prompt_path)
                if schema_version == 2:
                    inventory_path = relative_to_root(root, prompt_path)
                    inventory_item = inventory_by_path.get(inventory_path)
                    if inventory_item is None:
                        add(errors, f"{label}: prompt file is missing from inventory: {inventory_path}")
                    elif inventory_item.get("asset_role") != "segment_prompt":
                        add(errors, f"{label}: prompt inventory asset_role must be segment_prompt")
                    elif inventory_item.get("segment_id") != segment_id:
                        add(errors, f"{label}: prompt inventory segment_id does not match segment id")
                    prior_segment = next(
                        (
                            prior_id
                            for prior_id, prior_path in segment_prompt_paths.items()
                            if prior_path.resolve() == prompt_path.resolve()
                        ),
                        None,
                    )
                    if prior_segment is not None and prior_segment != segment_id:
                        add(
                            errors,
                            f"{label}: prompt path is already assigned to segment {prior_segment}",
                        )
                    else:
                        segment_prompt_paths[segment_id] = prompt_path
                try:
                    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
                except (OSError, UnicodeError) as exc:
                    add(errors, f"{label}: cannot read prompt file: {exc}")
                else:
                    if isinstance(prompt, str) and prompt_text != prompt.strip():
                        add(errors, f"{label}: prompt file content differs from manifest")

    if schema_version == 2:
        prompts_by_inventory_path = {
            relative_to_root(root, path): segment_id
            for segment_id, path in segment_prompt_paths.items()
        }
        for inventory_path, inventory_item in inventory_by_path.items():
            if inventory_item.get("asset_role") != "segment_prompt":
                continue
            if prompts_by_inventory_path.get(inventory_path) != inventory_item.get("segment_id"):
                add(
                    errors,
                    f"inventory {inventory_path}: segment_prompt does not match its segment prompt_filename",
                )

    if len(aspect_ratios) > 1:
        add(errors, f"segments use inconsistent aspect ratios: {sorted(aspect_ratios)}")

    try:
        calculated_total: float | None = math.fsum(durations)
    except OverflowError:
        calculated_total = None
        add(errors, "sum of segment durations overflows")
    if calculated_total is not None and not math.isfinite(calculated_total):
        calculated_total = None
        add(errors, "sum of segment durations must be finite")
    declared_total = data.get("total_seconds")
    if declared_total is None:
        add(errors, "top-level total_seconds is missing")
    elif (
        isinstance(declared_total, bool)
        or not isinstance(declared_total, (int, float))
        or not math.isfinite(float(declared_total))
    ):
        add(errors, "total_seconds must be a finite number")
    elif calculated_total is not None and abs(float(declared_total) - calculated_total) > 1e-6:
        add(errors, f"total_seconds {declared_total} does not equal segment sum {calculated_total:g}")

    delivery_receipt_path: Path | None = None
    receipt_verified = False
    receipt_hardened_verified = False
    delivery_evidence_version: int | None = None
    if schema_version == 2 and (
        args.delivery_receipt or args.require_feishu_delivery or args.require_hardened_feishu_delivery
    ):
        raw_receipt_path = Path(args.delivery_receipt).expanduser() if args.delivery_receipt else Path(DELIVERY_RECEIPT_FILENAME)
        delivery_receipt_path = raw_receipt_path if raw_receipt_path.is_absolute() else root / raw_receipt_path
        receipt_verified, receipt_hardened_verified, delivery_evidence_version = validate_delivery_receipt(
            delivery_receipt_path,
            root,
            manifest_path,
            segments,
            characters,
            scenes,
            images,
            voices,
            inventory_by_path,
            delivery_blockers,
            checked_files,
            warnings,
            args.require_hardened_feishu_delivery,
        )

    voice_blockers = voice_config_blockers + pending_voice_blockers
    generation_blockers = image_blockers + voice_blockers + character_blockers + scene_blockers
    blockers = generation_blockers + delivery_blockers
    structural_ok = (
        not errors
        and not image_blockers
        and not voice_config_blockers
        and not character_blockers
        and not scene_blockers
    )
    validation_passed = structural_ok and (
        not pending_voice_blockers or args.allow_pending_voices
    )
    ready_for_generation = structural_ok and not pending_voice_blockers
    ready_for_document_delivery = (
        schema_version == 2
        and structural_ok
        and (not pending_voice_blockers or args.allow_pending_voices)
    )
    ready_for_delivery = ready_for_document_delivery
    delivery_verified = ready_for_document_delivery and receipt_verified
    delivery_hardened_verified = ready_for_document_delivery and receipt_hardened_verified
    delivery_validation_requested = bool(
        args.require_feishu_delivery or args.require_hardened_feishu_delivery or args.delivery_receipt
    )
    requested_delivery_verified = (
        delivery_hardened_verified if args.require_hardened_feishu_delivery else delivery_verified
    )
    passed = validation_passed and (not delivery_validation_requested or requested_delivery_verified)
    result = {
        "passed": passed,
        "ready_for_generation": ready_for_generation,
        "ready_for_document_delivery": ready_for_document_delivery,
        "ready_for_delivery": ready_for_delivery,
        "delivery_verified": delivery_verified,
        "delivery_hardened_verified": delivery_hardened_verified,
        "delivery_evidence_version": delivery_evidence_version,
        "delivery_assurance": (
            "hardened_v2"
            if delivery_hardened_verified
            else "legacy"
            if delivery_verified
            else "none"
        ),
        "delivery_verification_scope": "local_readback_evidence_only",
        "delivery_receipt": str(delivery_receipt_path) if delivery_receipt_path is not None else None,
        "manifest": str(manifest_path),
        "root": str(root),
        "schema_version": schema_version,
        "segment_count": len(segments),
        "character_count": len(characters),
        "scene_count": len(scenes),
        "total_seconds": calculated_total,
        "aspect_ratios": sorted(aspect_ratios),
        "checked_file_count": len(checked_files),
        "errors": errors,
        "blockers": blockers,
        "blocker_types": {
            "images": image_blockers,
            "voices": voice_blockers,
            "voice_configuration": voice_config_blockers,
            "voice_pending": pending_voice_blockers,
            "characters": character_blockers,
            "scenes": scene_blockers,
            "delivery": delivery_blockers,
        },
        "warnings": warnings,
    }
    if errors:
        code = 1
    elif image_blockers:
        code = 2
    elif voice_config_blockers:
        code = 2
    elif character_blockers:
        code = 2
    elif scene_blockers:
        code = 2
    elif pending_voice_blockers and not args.allow_pending_voices:
        code = 2
    elif delivery_validation_requested and not requested_delivery_verified:
        code = 2
    else:
        code = 0
    return result, code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="path to the production manifest JSON")
    parser.add_argument("--root", help="project root; defaults to the manifest directory")
    parser.add_argument(
        "--image-dir",
        help="image directory under root; defaults to project root for v2 and 参考图 for v1",
    )
    parser.add_argument(
        "--voice-dir",
        help="voice directory under root; defaults to 05_声样 for v2 and 声样 for v1",
    )
    parser.add_argument(
        "--prompt-dir",
        help="prompt directory under root; defaults to 06_逐段提示词 for v2 and 逐段提示词 for v1",
    )
    parser.add_argument("--min-duration", type=float, default=8.0)
    parser.add_argument("--max-duration", type=float, default=10.0)
    parser.add_argument("--aspect", help="required aspect ratio, for example 9:16")
    parser.add_argument("--dialogue-rate", type=float, default=3.0, help="warning threshold in CJK characters per second")
    parser.add_argument(
        "--allow-pending-voices",
        action="store_true",
        help="allow explicit pending voice entries; v2 can pass document delivery while generation stays blocked",
    )
    parser.add_argument("--allow-background-music", action="store_true", help="do not require no-background-music in every prompt")
    parser.add_argument(
        "--delivery-receipt",
        help=f"path to project-root {DELIVERY_RECEIPT_FILENAME}; defaults to that filename when delivery is required",
    )
    parser.add_argument(
        "--require-feishu-delivery",
        action="store_true",
        help="require schema v2 and a verified Feishu receipt; legacy receipt schema v1 remains accepted",
    )
    parser.add_argument(
        "--require-hardened-feishu-delivery",
        action="store_true",
        help="require receipt schema v2 with exact remote segment bindings, voice playback, visual evidence, and cloud ZIP hash",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result, code = validate(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
