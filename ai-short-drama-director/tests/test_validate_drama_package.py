#!/usr/bin/env python3
"""Smoke tests for the public short-drama package validator.

The fixtures are intentionally built at runtime with Python's standard library
so the tests neither depend on the private example project nor check in binary
media.  Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
import zipfile


SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL_ROOT / "scripts" / "validate_drama_package.py"
MANIFEST_REL = Path("07_映射与校验/镜头与素材映射.json")
RECEIPT_REL = Path("飞书交付回执.json")
EVIDENCE_REL = Path("07_映射与校验/飞书回读证据.json")
ARCHIVE_REL = Path("第一集_AI短剧素材包.zip")
CLOUD_ARCHIVE_REL = Path("交付证据/飞书下载素材包.zip")
VISUAL_EVIDENCE_REL = Path("交付证据/飞书页面预览.png")
DOCUMENT_ID = "DocToken123"
ATTACHMENT_TOKEN = "AttachmentToken123"
STAMP = "2026-10-12T00:00:00+08:00"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n").strip(),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _write_bytes(root: Path, relative: str | Path, content: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _write_text(root: Path, relative: str | Path, content: str) -> Path:
    return _write_bytes(root, relative, content.encode("utf-8"))


def _write_json(root: Path, relative: str | Path, value: object) -> Path:
    return _write_text(
        root,
        relative,
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )


def _inventory_item(
    root: Path,
    relative: str,
    role: str,
    **binding: str,
) -> dict[str, str]:
    return {
        "path": relative,
        "sha256": _sha256(root / relative),
        "asset_role": role,
        **binding,
    }


def build_minimal_package(root: Path) -> dict[str, object]:
    """Create a structurally valid v2 package with one pending voice."""
    title = "第一集：系统降临"
    outline_rel = "01_剧情/已确认大纲.md"
    contract_rel = "01_剧情/故事契约.json"
    character_image_rel = "02_人物圣经/C1/C1-三视图.png"
    scene_image_rel = "03_场景圣经/S1/S1-无人场景.png"
    prompt_rel = "06_逐段提示词/SEG-01.txt"

    _write_text(root, outline_rel, "# 已确认大纲\n\n林川失业当天收到系统任务。\n")
    _write_text(root, "01_剧情/完整脚本.md", "# 完整脚本\n\n见逐段生成表。\n")
    _write_text(root, "01_剧情/连续性台账.md", "# 连续性台账\n\n手机始终在林川右手。\n")
    _write_text(root, "02_人物圣经/C1/C1-人物卡.md", "# C1 林川\n\n刚毕业失业的成年男性。\n")
    # The validator checks the PNG signature; distinct payloads prevent an
    # accidental same-asset shortcut between character and scene references.
    _write_bytes(root, character_image_rel, b"\x89PNG\r\n\x1a\ncharacter-c1")
    _write_text(root, "02_人物圣经/C1/C1-三视图提示词.txt", "林川单人三视图，9:16。\n")
    _write_text(root, "03_场景圣经/S1/S1-场景卡.md", "# S1 出租屋\n\n狭小、整洁的无人出租屋。\n")
    _write_bytes(root, scene_image_rel, b"\x89PNG\r\n\x1a\nscene-s1")
    _write_text(root, "03_场景圣经/S1/S1-无人场景提示词.txt", "无人出租屋空间锚点，9:16。\n")
    _write_text(root, "07_映射与校验/逐段生成表.md", "# 逐段生成表\n\nSEG-01，10 秒。\n")
    _write_text(root, "07_映射与校验/校验结果.json", "{}\n")

    prompt = (
        "10秒，9:16，现代都市真人写实短剧。"
        "@图片1只锁定林川身份与服装；@图片2只锁定出租屋空间。"
        "@音频1只参考林川音色。林川先低头看拒信，手机震动后抬眼，"
        "从疲惫转为警觉。环境声为纸张摩擦与手机震动，无背景音乐。"
    )
    _write_text(root, prompt_rel, prompt + "\n")

    confirmed_outline = {
        "filename": outline_rel,
        "sha256": _sha256(root / outline_rel),
        "confirmed_at": STAMP,
        "evidence": "用户在当前会话明确确认该版大纲。",
    }
    contract = {
        "title": title,
        "confirmed_outline": confirmed_outline,
        "expected_character_ids": ["C1"],
        "expected_core_scene_ids": ["S1"],
        "expected_segment_ids": ["SEG-01"],
        "aspect_ratio": "9:16",
    }
    _write_json(root, contract_rel, contract)

    inventory = [
        _inventory_item(root, contract_rel, "story_contract"),
        _inventory_item(root, outline_rel, "confirmed_outline"),
        _inventory_item(root, "01_剧情/完整脚本.md", "complete_script"),
        _inventory_item(root, "01_剧情/连续性台账.md", "continuity_ledger"),
        _inventory_item(root, "02_人物圣经/C1/C1-人物卡.md", "character_card", owner_id="C1"),
        _inventory_item(root, character_image_rel, "character_image", owner_id="C1"),
        _inventory_item(
            root,
            "02_人物圣经/C1/C1-三视图提示词.txt",
            "character_image_prompt",
            owner_id="C1",
        ),
        _inventory_item(root, "03_场景圣经/S1/S1-场景卡.md", "scene_card", scene_id="S1"),
        _inventory_item(root, scene_image_rel, "scene_image", scene_id="S1"),
        _inventory_item(
            root,
            "03_场景圣经/S1/S1-无人场景提示词.txt",
            "scene_image_prompt",
            scene_id="S1",
        ),
        _inventory_item(root, prompt_rel, "segment_prompt", segment_id="SEG-01"),
        _inventory_item(root, "07_映射与校验/逐段生成表.md", "segment_generation_table"),
        _inventory_item(root, "07_映射与校验/校验结果.json", "validation_result"),
    ]

    manifest: dict[str, object] = {
        "schema_version": 2,
        "title": title,
        "total_seconds": 10,
        "story_contract": {
            "filename": contract_rel,
            "sha256": _sha256(root / contract_rel),
        },
        "requirements": {
            "expected_character_count": 1,
            "expected_core_scene_count": 1,
            "expected_segment_count": 1,
            "expected_character_ids": ["C1"],
            "expected_core_scene_ids": ["S1"],
            "expected_segment_ids": ["SEG-01"],
            "aspect_ratio": "9:16",
        },
        "characters": {
            "C1": {
                "name": "林川",
                "character_class": "protagonist",
                "narrative_role": "男主",
                "profile": "刚毕业失业，谨慎但有韧性。",
                "appearance": "成年中国男性，黑色短发，灰色连帽衫。",
                "reference_image_ids": ["IMG-C1"],
                "ready": True,
            }
        },
        "scenes": {
            "S1": {
                "name": "出租屋",
                "scene_class": "core",
                "description": "狭小整洁的出租屋，无人基础锚点。",
                "reference_image_ids": ["IMG-S1"],
                "ready": True,
            }
        },
        "images": {
            "IMG-C1": {
                "filename": character_image_rel,
                "asset_type": "character_turnaround",
                "owner_id": "C1",
                "purpose": "锁定林川的身份、脸、发型和服装",
                "prohibited_use": "不得复制为其他人物或场景",
                "source": "AI 生成后人工预览",
                "qc_status": "passed",
                "ready": True,
                "sha256": _sha256(root / character_image_rel),
            },
            "IMG-S1": {
                "filename": scene_image_rel,
                "asset_type": "scene_anchor",
                "scene_id": "S1",
                "purpose": "锁定出租屋结构、机位基准和光线",
                "prohibited_use": "不得冒充其他场景，不固化剧情人物",
                "source": "AI 生成后人工预览",
                "qc_status": "passed",
                "ready": True,
                "sha256": _sha256(root / scene_image_rel),
            },
        },
        "voices": {
            "V-C1": {
                "character_id": "C1",
                "role": "林川",
                "ready": False,
                "qc_status": "pending",
                "pending_reason": "尚未授权进入平台生成单人参考视频并提取声样。",
            }
        },
        "segments": [
            {
                "id": "SEG-01",
                "title": "系统降临",
                "duration_seconds": 10,
                "aspect_ratio": "9:16",
                "character_ids": ["C1"],
                "scene_id": "S1",
                "images": ["IMG-C1", "IMG-S1"],
                "voices": ["V-C1"],
                "start": "林川坐在空桌前看拒信，肩膀下沉。",
                "visual": "手机震动，他从失落转为警觉。",
                "end": "林川握紧手机，目光坚定。",
                "change": "他获得第一次行动目标。",
                "sound": "纸张摩擦、手机震动、系统提示音，无背景音乐。",
                "lines": [
                    {
                        "character_id": "C1",
                        "role": "林川",
                        "text": "又被拒了。",
                        "emotion": "压抑、疲惫",
                    }
                ],
                "prompt": prompt,
                "prompt_filename": "SEG-01.txt",
            }
        ],
        "required_files": [item["path"] for item in inventory],
        "inventory": inventory,
    }
    _write_json(root, MANIFEST_REL, manifest)
    return manifest


def _archive_members(root: Path, manifest: dict[str, object]) -> list[str]:
    inventory = manifest["inventory"]
    assert isinstance(inventory, list)
    members = [str(item["path"]) for item in inventory if isinstance(item, dict)]
    return members + [MANIFEST_REL.as_posix()]


def _create_archive(
    root: Path,
    manifest: dict[str, object],
    *,
    unsafe_member: bool = False,
) -> int:
    archive_path = root / ARCHIVE_REL
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    members = _archive_members(root, manifest)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in members:
            archive.write(root / relative, arcname=relative)
        if unsafe_member:
            archive.writestr("../escape.txt", "must never be extracted")
    cloud_path = root / CLOUD_ARCHIVE_REL
    cloud_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive_path, cloud_path)
    return len(members) + int(unsafe_member)


def _delivery_counts() -> dict[str, int]:
    return {
        "character_card_count": 1,
        "turnaround_count": 1,
        "identity_image_count": 0,
        "system_ui_count": 0,
        "scene_card_count": 1,
        "scene_anchor_count": 1,
        "scene_variant_count": 0,
        "voice_sample_count": 0,
        "pending_voice_count": 1,
        "segment_row_count": 1,
        "prompt_count": 1,
        "archive_count": 1,
    }


def _sync_delivery_artifacts(root: Path, manifest: dict[str, object], *, unsafe_member: bool = False) -> None:
    """Create a self-consistent hardened receipt around the current ZIP."""
    file_count = _create_archive(root, manifest, unsafe_member=unsafe_member)
    archive_path = root / ARCHIVE_REL
    archive_sha = _sha256(archive_path)
    archive_size = archive_path.stat().st_size

    visual_path = _write_bytes(
        root,
        VISUAL_EVIDENCE_REL,
        b"\x89PNG\r\n\x1a\nfeishu-page-preview",
    )
    segment = manifest["segments"][0]  # type: ignore[index]
    assert isinstance(segment, dict)
    segment_readback = {
        "SEG-01": {
            "row_id": "row-segment-01",
            "prompt_block_id": "block-prompt-01",
            "prompt_sha256": _text_sha256(str(segment["prompt"])),
            "duration_seconds": 10,
            "aspect_ratio": "9:16",
            "image_upload_order": ["IMG-C1", "IMG-S1"],
            "voice_upload_order": ["V-C1"],
        }
    }
    voice_playback = {
        "V-C1": {
            "block_id": "block-voice-c1",
            "status": "pending",
            "reason": "尚未授权生成单人参考视频并提取声样。",
        }
    }
    visual_preview = {
        "filename": VISUAL_EVIDENCE_REL.as_posix(),
        "sha256": _sha256(visual_path),
        "size_bytes": visual_path.stat().st_size,
        "captured_at": STAMP,
        "document_id": DOCUMENT_ID,
        "revision": "7",
        "coverage": [
            "document_header",
            "archive",
            "character:C1",
            "scene:S1",
            "image:IMG-C1",
            "image:IMG-S1",
            "voice:V-C1",
            "segment:SEG-01",
        ],
    }
    cloud_download = {
        "filename": CLOUD_ARCHIVE_REL.as_posix(),
        "sha256": archive_sha,
        "size_bytes": archive_size,
        "downloaded_at": STAMP,
        "attachment_token": ATTACHMENT_TOKEN,
    }
    block_ids = {
        "image_block_ids": {
            "IMG-C1": "block-image-c1",
            "IMG-S1": "block-image-s1",
        },
        "voice_block_ids": {"V-C1": "block-voice-c1"},
        "segment_row_ids": {"SEG-01": "row-segment-01"},
        "archive_attachment_token": ATTACHMENT_TOKEN,
    }
    evidence = {
        "delivery_evidence_version": 2,
        "prompt_hash_profile": "sha256:utf8-nfc-lf-strip-v1",
        "document_id": DOCUMENT_ID,
        "readback_evidence_id": "readback-20261012-000000",
        "revision": "7",
        "counts": _delivery_counts(),
        "block_ids": block_ids,
        "segment_readback": segment_readback,
        "voice_playback_verification": voice_playback,
        "visual_preview_evidence": visual_preview,
        "cloud_archive_download": cloud_download,
    }
    evidence_path = _write_json(root, EVIDENCE_REL, evidence)
    receipt = {
        "delivery_evidence_version": 2,
        "prompt_hash_profile": "sha256:utf8-nfc-lf-strip-v1",
        "platform": "feishu_doc",
        "status": "verified",
        "document_url": f"https://example.feishu.cn/docx/{DOCUMENT_ID}",
        "document_id": DOCUMENT_ID,
        "verified_at": STAMP,
        "readback_evidence_id": "readback-20261012-000000",
        "revision": "7",
        "readback_evidence": {
            "filename": EVIDENCE_REL.as_posix(),
            "sha256": _sha256(evidence_path),
            "size_bytes": evidence_path.stat().st_size,
        },
        "previewed_image_ids": ["IMG-C1", "IMG-S1"],
        "previewed_voice_ids": [],
        "segment_readback": segment_readback,
        "voice_playback_verification": voice_playback,
        "visual_preview_evidence": visual_preview,
        "current_archive": {
            "filename": ARCHIVE_REL.as_posix(),
            "sha256": archive_sha,
            "size_bytes": archive_size,
            "file_count": file_count,
            "attachment_token": ATTACHMENT_TOKEN,
            "cloud_size_bytes": archive_size,
            "cloud_download": cloud_download,
        },
        "readback": {
            **_delivery_counts(),
            "archive_filename": ARCHIVE_REL.as_posix(),
            "attachment_token": ATTACHMENT_TOKEN,
            "cloud_size_bytes": archive_size,
        },
    }
    _write_json(root, RECEIPT_REL, receipt)


def _rewrite_delivery_evidence(
    root: Path,
    mutate: object,
) -> None:
    """Mutate receipt/evidence together and refresh the evidence file binding."""
    receipt_path = root / RECEIPT_REL
    evidence_path = root / EVIDENCE_REL
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not callable(mutate):
        raise TypeError("mutate must be callable")
    mutate(receipt, evidence)
    _write_json(root, EVIDENCE_REL, evidence)
    receipt["readback_evidence"]["sha256"] = _sha256(evidence_path)
    receipt["readback_evidence"]["size_bytes"] = evidence_path.stat().st_size
    _write_json(root, RECEIPT_REL, receipt)


def run_validator(root: Path, *, delivery: bool = False) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    command = [
        sys.executable,
        str(VALIDATOR),
        str(root / MANIFEST_REL),
        "--root",
        str(root),
        "--aspect",
        "9:16",
        "--allow-pending-voices",
    ]
    if delivery:
        command.extend(
            [
                "--delivery-receipt",
                RECEIPT_REL.as_posix(),
                "--require-hardened-feishu-delivery",
            ]
        )
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - assertion aid
        raise AssertionError(
            f"validator did not emit JSON\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        ) from exc
    return completed, result


class ValidatorSmokeTests(unittest.TestCase):
    def make_package(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, object]]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        return temporary, root, build_minimal_package(root)

    def assert_message_contains(self, messages: object, needle: str) -> None:
        self.assertIsInstance(messages, list)
        self.assertTrue(
            any(needle in str(message) for message in messages),
            f"expected {needle!r} in {messages!r}",
        )

    def assert_hardened_delivery(self, result: dict[str, object]) -> None:
        self.assertTrue(result["delivery_verified"])
        self.assertTrue(result["delivery_hardened_verified"])
        self.assertEqual(result["delivery_evidence_version"], 2)
        self.assertEqual(result["delivery_assurance"], "hardened_v2")

    def test_valid_pending_voice_package_passes_document_gate(self) -> None:
        temporary, root, _manifest = self.make_package()
        self.addCleanup(temporary.cleanup)

        completed, result = run_validator(root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(result["passed"])
        self.assertTrue(result["ready_for_document_delivery"])
        self.assertFalse(result["ready_for_generation"])
        self.assertEqual(result["errors"], [])
        self.assert_message_contains(
            result["blocker_types"]["voice_pending"],  # type: ignore[index]
            "referenced but pending",
        )

    def test_missing_image_is_a_structural_error(self) -> None:
        temporary, root, _manifest = self.make_package()
        self.addCleanup(temporary.cleanup)
        (root / "02_人物圣经/C1/C1-三视图.png").unlink()

        completed, result = run_validator(root)

        self.assertEqual(completed.returncode, 1)
        self.assertFalse(result["passed"])
        self.assertFalse(result["ready_for_document_delivery"])
        self.assert_message_contains(result["errors"], "file not found")

    def test_inventory_hash_mismatch_is_a_structural_error(self) -> None:
        temporary, root, _manifest = self.make_package()
        self.addCleanup(temporary.cleanup)
        with (root / "01_剧情/完整脚本.md").open("a", encoding="utf-8") as handle:
            handle.write("篡改内容。\n")

        completed, result = run_validator(root)

        self.assertEqual(completed.returncode, 1)
        self.assertFalse(result["passed"])
        self.assertFalse(result["ready_for_document_delivery"])
        self.assert_message_contains(result["errors"], "sha256 mismatch")

    def test_receipt_archive_hash_mismatch_blocks_delivery(self) -> None:
        temporary, root, manifest = self.make_package()
        self.addCleanup(temporary.cleanup)
        _sync_delivery_artifacts(root, manifest)

        baseline, baseline_result = run_validator(root, delivery=True)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assert_hardened_delivery(baseline_result)

        receipt_path = root / RECEIPT_REL
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["current_archive"]["sha256"] = "0" * 64
        _write_json(root, RECEIPT_REL, receipt)

        completed, result = run_validator(root, delivery=True)

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(result["passed"])
        self.assertFalse(result["delivery_verified"])
        self.assert_message_contains(
            result["blocker_types"]["delivery"],  # type: ignore[index]
            "current_archive sha256 mismatch",
        )

    def test_zip_path_traversal_blocks_delivery(self) -> None:
        temporary, root, manifest = self.make_package()
        self.addCleanup(temporary.cleanup)
        _sync_delivery_artifacts(root, manifest)

        baseline, baseline_result = run_validator(root, delivery=True)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assert_hardened_delivery(baseline_result)

        # Rebuild the local and cloud-download copies together, then regenerate
        # the receipt hashes.  The only remaining fault is the unsafe member.
        _sync_delivery_artifacts(root, manifest, unsafe_member=True)
        completed, result = run_validator(root, delivery=True)

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(result["passed"])
        self.assertFalse(result["delivery_verified"])
        self.assert_message_contains(
            result["blocker_types"]["delivery"],  # type: ignore[index]
            "unsafe ZIP member path: ../escape.txt",
        )

    def test_remote_prompt_hash_tamper_blocks_hardened_delivery(self) -> None:
        temporary, root, manifest = self.make_package()
        self.addCleanup(temporary.cleanup)
        _sync_delivery_artifacts(root, manifest)

        baseline, baseline_result = run_validator(root, delivery=True)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assert_hardened_delivery(baseline_result)

        def tamper_prompt_hash(receipt: dict[str, object], evidence: dict[str, object]) -> None:
            receipt["segment_readback"]["SEG-01"]["prompt_sha256"] = "0" * 64  # type: ignore[index]
            evidence["segment_readback"]["SEG-01"]["prompt_sha256"] = "0" * 64  # type: ignore[index]

        _rewrite_delivery_evidence(root, tamper_prompt_hash)
        completed, result = run_validator(root, delivery=True)

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(result["passed"])
        self.assertFalse(result["delivery_hardened_verified"])
        self.assertEqual(result["delivery_assurance"], "none")
        self.assert_message_contains(
            result["blocker_types"]["delivery"],  # type: ignore[index]
            "prompt_sha256 does not match manifest prompt",
        )

    def test_missing_voice_playback_record_blocks_hardened_delivery(self) -> None:
        temporary, root, manifest = self.make_package()
        self.addCleanup(temporary.cleanup)
        _sync_delivery_artifacts(root, manifest)

        baseline, baseline_result = run_validator(root, delivery=True)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assert_hardened_delivery(baseline_result)

        def remove_voice_record(receipt: dict[str, object], evidence: dict[str, object]) -> None:
            receipt["voice_playback_verification"].pop("V-C1")  # type: ignore[union-attr]
            evidence["voice_playback_verification"].pop("V-C1")  # type: ignore[union-attr]

        _rewrite_delivery_evidence(root, remove_voice_record)
        completed, result = run_validator(root, delivery=True)

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(result["passed"])
        self.assertFalse(result["delivery_hardened_verified"])
        self.assertEqual(result["delivery_assurance"], "none")
        self.assert_message_contains(
            result["blocker_types"]["delivery"],  # type: ignore[index]
            "voice_playback_verification must cover every manifest voice ID exactly once",
        )


if __name__ == "__main__":
    unittest.main()
