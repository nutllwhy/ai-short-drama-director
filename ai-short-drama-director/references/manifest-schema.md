# 制作清单 Schema

`镜头与素材映射.json` 是短剧前期包的生产清单。新项目使用 `schema_version: 2`；旧项目可继续使用 v1。

v2 有三层相互校验的事实源：

1. `01_剧情/故事契约.json` 保存用户已确认的标题、大纲哈希、人物 ID、核心场景 ID、分段 ID 和画幅。它独立于生产清单，防止只改 manifest 内的 `requirements` 来静默缩水。
2. `镜头与素材映射.json` 必须引用故事契约并逐项列出人物、场景、图片、声样、分段和 inventory 实体归属。
3. 飞书写入并读回后，项目根目录的 `飞书交付回执.json` 和独立 JSON 回读证据必须同时绑定文档身份、分类计数、远端块 ID 和当前 ZIP。

ZIP 内放生产清单和 inventory 文件，不放 `飞书交付回执.json`。这样不会产生把 ZIP 自身哈希写回 ZIP 的自引用闭环。

## v2 默认路径

未显式传目录参数时：

- 图片 `filename` 从项目根目录解析，通常在 `02_人物圣经/` 或 `03_场景圣经/`。
- 声样 `filename` 从 `05_声样/` 解析。
- 分段 `prompt_filename` 从 `06_逐段提示词/` 解析。
- `story_contract.filename`、`required_files`、`inventory[].path`、交付证据、ZIP 和回执路径始终相对项目根目录。

`--image-dir`、`--voice-dir`、`--prompt-dir` 可覆盖默认目录；实体绑定仍使用解析后的项目根相对路径校验。

## 独立故事契约

推荐文件名为 `01_剧情/故事契约.json`：

```json
{
  "title": "第一集：系统降临",
  "confirmed_outline": {
    "filename": "01_剧情/已确认大纲.md",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "confirmed_at": "2026-10-11T20:00:00+08:00",
    "evidence": "用户在当前会话明确确认该版大纲。"
  },
  "expected_character_ids": ["C1"],
  "expected_core_scene_ids": ["S1"],
  "expected_segment_ids": ["SEG-01"],
  "aspect_ratio": "9:16"
}
```

契约规则：

- 三个 ID 数组必须非空、只含非空字符串且不得重复。
- `confirmed_outline` 必须指向 inventory 中 `asset_role: confirmed_outline` 的真实文件，文件哈希、契约哈希和 inventory 哈希必须一致。
- `confirmed_at` 使用带时区 RFC 3339 时间；`evidence` 记录用户确认依据。
- 生产清单的 `title`、`requirements`、实际人物集合、实际核心场景集合、实际分段集合和每段画幅必须与契约精确一致。
- 生产清单若为迁移兼容而保留顶层 `confirmed_outline`，其对象必须与契约中的 `confirmed_outline` 完全相同。新清单可省略这份重复字段。

该本地哈希链用于阻止只改生产 manifest 的静默缩水。若连故事契约、确认大纲和全部哈希一起重写，本地文件本身不能替代外部版本历史或签名证明。

## v2 生产清单示例

示例哈希仅为占位；实际清单必须写真实 SHA-256。

```json
{
  "schema_version": 2,
  "title": "第一集：系统降临",
  "total_seconds": 10,
  "story_contract": {
    "filename": "01_剧情/故事契约.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "requirements": {
    "expected_character_count": 1,
    "expected_core_scene_count": 1,
    "expected_segment_count": 1,
    "expected_character_ids": ["C1"],
    "expected_core_scene_ids": ["S1"],
    "expected_segment_ids": ["SEG-01"],
    "aspect_ratio": "9:16"
  },
  "characters": {
    "C1": {
      "name": "林川",
      "character_class": "protagonist",
      "narrative_role": "男主",
      "profile": "刚毕业失业，谨慎但有韧性。",
      "appearance": "成年中国男性，黑色短发，灰色连帽衫。",
      "reference_image_ids": ["IMG-C1"],
      "ready": true
    }
  },
  "scenes": {
    "S1": {
      "name": "出租屋",
      "scene_class": "core",
      "description": "狭小整洁的出租屋，无人基础锚点。",
      "reference_image_ids": ["IMG-S1"],
      "ready": true
    }
  },
  "images": {
    "IMG-C1": {
      "filename": "02_人物圣经/C1-三视图.png",
      "asset_type": "character_turnaround",
      "owner_id": "C1",
      "purpose": "锁定林川的身份、脸、发型和服装",
      "prohibited_use": "不得复制为其他人物或场景",
      "source": "AI 生成后人工预览",
      "qc_status": "passed",
      "ready": true,
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    },
    "IMG-S1": {
      "filename": "03_场景圣经/S1-无人场景.png",
      "asset_type": "scene_anchor",
      "scene_id": "S1",
      "purpose": "锁定出租屋结构、机位基准和光线",
      "prohibited_use": "不得冒充其他场景，不固化剧情人物",
      "source": "AI 生成后人工预览",
      "qc_status": "passed",
      "ready": true,
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  },
  "voices": {
    "V-C1": {
      "filename": "C1.wav",
      "character_id": "C1",
      "role": "林川",
      "source": "角色单人视频提取并试听",
      "qc_status": "passed",
      "ready": true,
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
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
      "sound": "纸张摩擦、手机震动、系统提示音，无配乐。",
      "lines": [
        {"character_id": "C1", "role": "林川", "text": "又被拒了。", "emotion": "压抑、疲惫"}
      ],
      "prompt": "10秒，9:16，@图片1锁定林川，@图片2锁定出租屋，@音频1只参考林川音色；无背景音乐。",
      "prompt_filename": "SEG-01.txt"
    }
  ],
  "required_files": [
    "01_剧情/故事契约.json",
    "01_剧情/已确认大纲.md",
    "01_剧情/完整脚本.md",
    "01_剧情/连续性台账.md",
    "02_人物圣经/C1-人物卡.md",
    "02_人物圣经/C1-三视图.png",
    "02_人物圣经/C1-生图提示词.md",
    "03_场景圣经/S1-场景卡.md",
    "03_场景圣经/S1-无人场景.png",
    "03_场景圣经/S1-生图提示词.md",
    "05_声样/C1.wav",
    "06_逐段提示词/SEG-01.txt",
    "07_映射与校验/逐段生成表.md",
    "07_映射与校验/校验结果.json"
  ],
  "inventory": [
    {"path": "01_剧情/故事契约.json", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "story_contract"},
    {"path": "01_剧情/已确认大纲.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "confirmed_outline"},
    {"path": "01_剧情/完整脚本.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "complete_script"},
    {"path": "01_剧情/连续性台账.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "continuity_ledger"},
    {"path": "02_人物圣经/C1-人物卡.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "character_card", "owner_id": "C1"},
    {"path": "02_人物圣经/C1-三视图.png", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "character_image", "owner_id": "C1"},
    {"path": "02_人物圣经/C1-生图提示词.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "character_image_prompt", "owner_id": "C1"},
    {"path": "03_场景圣经/S1-场景卡.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "scene_card", "scene_id": "S1"},
    {"path": "03_场景圣经/S1-无人场景.png", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "scene_image", "scene_id": "S1"},
    {"path": "03_场景圣经/S1-生图提示词.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "scene_image_prompt", "scene_id": "S1"},
    {"path": "05_声样/C1.wav", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "voice_sample", "character_id": "C1"},
    {"path": "06_逐段提示词/SEG-01.txt", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "segment_prompt", "segment_id": "SEG-01"},
    {"path": "07_映射与校验/逐段生成表.md", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "segment_generation_table"},
    {"path": "07_映射与校验/校验结果.json", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "asset_role": "validation_result"}
  ]
}
```

## 人物、场景和图片

`character_class` 可为 `protagonist`、`antagonist`、`recurring_supporting`、`one_off_named` 或 `system_entity`。

- 主角、反派和常驻配角必须有专属 `character_turnaround`。
- 一次性具名角色必须有专属 `character_identity`。
- `system_entity` 必须写布尔值 `visible_ui`；`visible_ui: true` 时必须有专属 `system_ui_anchor`。非实体系统不要求人物图，但仍需要独立声样或显式待补声样记录。
- 人物专属图用 `owner_id`；场景图用 `scene_id`；分镜图用 `character_ids` 与 `scene_id`；道具图用稳定道具 `owner_id`。

`scene_class` 可为 `core`、`minor` 或 `variant`。核心场景必须有专属 `scene_anchor`；次要场景与变体必须有专属 `scene_variant`。

每张 v2 图片还必须有 `purpose`、`prohibited_use`、`source`、`qc_status: passed`、`ready: true` 和匹配的 inventory 文件。校验器检查 PNG/JPEG/WebP 文件头；人物专属图与核心场景锚点不能用同一内容冒充不同实体。

每个 v2 分段必须使用字符串 ID，并满足：

- `character_ids` 非空，`scene_id` 有效，`images` 非空。
- 图片实际覆盖段内每个实体人物和场景；不可见的 `system_entity` 可免图片覆盖。
- 每条 `lines[]` 有 `character_id`；开口人物有本段声样引用。
- prompt 包含时长、比例、“无背景音乐”，并按数组顺序完整包含连续的 `@图片1..N` 和 `@音频1..N`。
- `prompt_filename` 内容与 manifest 中 `prompt` 完全一致。

## inventory 实体绑定

`required_files` 与 `inventory[].path` 必须是同一组项目根相对路径。每个 inventory 项都有真实 SHA-256。下列角色必须带指定绑定键：

| `asset_role` | 绑定键 | 约束 |
|---|---|---|
| `story_contract` | 无 | 恰好一份，并与 manifest `story_contract` 的路径和哈希一致 |
| `character_card` | `owner_id` | 每个角色恰好一份 |
| `character_image` | `owner_id` | 路径必须对应同 owner 的 `character_turnaround` 或 `character_identity` |
| `character_image_prompt` | `owner_id` | 每个实体人物恰好一份 |
| `system_ui_image` | `owner_id` | 路径必须对应同 owner 的 `system_ui_anchor` |
| `system_ui_prompt` | `owner_id` | 每个可见系统实体恰好一份 |
| `scene_card` | `scene_id` | 每个场景恰好一份 |
| `scene_image` | `scene_id` | 路径必须对应同场景的 `scene_anchor` 或 `scene_variant` |
| `scene_image_prompt` | `scene_id` | 每个场景恰好一份 |
| `voice_sample` | `character_id` | 路径必须对应同角色且 `ready: true` 的声样 |
| `segment_prompt` | `segment_id` | 每段恰好一份，路径必须等于该段解析后的 `prompt_filename` |

绑定键互斥：人物类使用 `owner_id`，场景类使用 `scene_id`，声样使用 `character_id`，逐段提示词使用 `segment_id`。额外塞入无对应 manifest 资产的图片、声样或分段提示词会失败。

其余合法 `asset_role` 为：`confirmed_outline`、`complete_script`、`continuity_ledger`、`storyboard_image`、`prop_image`、`segment_generation_table`、`validation_result`、`supporting_file`。

生产 manifest 可进入 ZIP，但不进入 inventory，避免 manifest 自身哈希递归。`飞书交付回执.json` 不得进入 `required_files`、inventory 或 ZIP。

## 已就绪与待补声样

已就绪声样必须包含 `filename`、`character_id`、`source`、`qc_status: passed`、`ready: true`，真实文件在 inventory 中标为 `voice_sample`。校验器检查音频扩展名、文件头、哈希和实体归属；不同角色不得共用同一路径或同一内容哈希。

未授权进入平台生成声样时，可显式保留：

```json
"V-SYS": {
  "character_id": "SYS",
  "role": "系统",
  "ready": false,
  "qc_status": "pending",
  "pending_reason": "尚未授权进入平台生成并提取系统声样。"
}
```

待补条目必须有 `character_id`、`ready: false` 和非空 `pending_reason`；`qc_status` 若出现只能为 `pending`。不得写 `filename`、`sha256`、`size_bytes`，也不得为它创建 `voice_sample` inventory 占位文件。

- 不传 `--allow-pending-voices` 时，任何待补声样都会阻断。
- 传入该参数后，结构完整的 v2 包可达到 `ready_for_document_delivery: true`，但 `ready_for_generation` 仍为 `false`。
- 待补声样可保留在分段 `voices` 上传计划中；该分段仍不可提交生成。

## 飞书回读证据与回执

生产 manifest 继续使用 `schema_version: 2`。新飞书交付在包外回执和回读证据中使用 `delivery_evidence_version: 2`；旧回执缺省按 evidence v1 兼容。证据文件、视觉预览文件和云端回下载 ZIP 都位于包外，不进入 inventory 或素材 ZIP。

回读证据必须是独立 JSON，例如 `交付证据/飞书回读证据.json`。下例省略了部分重复计数，但真实文件必须保留全部 12 项：

```json
{
  "delivery_evidence_version": 2,
  "prompt_hash_profile": "sha256:utf8-nfc-lf-strip-v1",
  "document_id": "DocToken123",
  "readback_evidence_id": "request_20261012_210000",
  "revision": "42",
  "counts": {
    "character_card_count": 1,
    "turnaround_count": 1,
    "identity_image_count": 0,
    "system_ui_count": 0,
    "scene_card_count": 1,
    "scene_anchor_count": 1,
    "scene_variant_count": 0,
    "voice_sample_count": 1,
    "pending_voice_count": 0,
    "segment_row_count": 1,
    "prompt_count": 1,
    "archive_count": 1
  },
  "image_block_ids": {"IMG-C1": "blk_image_c1", "IMG-S1": "blk_image_s1"},
  "voice_block_ids": {"V-C1": "blk_voice_c1"},
  "segment_row_ids": {"SEG-01": "row_segment_01"},
  "archive_attachment_token": "boxcnAttachmentToken",
  "segment_readback": {
    "SEG-01": {
      "row_id": "row_segment_01",
      "prompt_block_id": "blk_prompt_seg01",
      "prompt_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "duration_seconds": 10,
      "aspect_ratio": "9:16",
      "image_upload_order": ["IMG-C1", "IMG-S1"],
      "voice_upload_order": ["V-C1"]
    }
  },
  "voice_playback_verification": {
    "V-C1": {
      "status": "played",
      "block_id": "blk_voice_c1",
      "media_token": "boxcnVoiceToken",
      "verified_at": "2026-10-12T20:55:00+08:00",
      "duration_seconds": 8.6,
      "audible": true,
      "correct_character": true,
      "natural": true,
      "no_background_music": true
    }
  },
  "visual_preview_evidence": {
    "filename": "交付证据/飞书视觉预览.pdf",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 123456,
    "captured_at": "2026-10-12T21:00:00+08:00",
    "document_id": "DocToken123",
    "revision": "42",
    "coverage": [
      "document_header", "archive", "character:C1", "scene:S1",
      "image:IMG-C1", "image:IMG-S1", "voice:V-C1", "segment:SEG-01"
    ]
  },
  "cloud_archive_download": {
    "filename": "交付证据/云端回下载/第一集-AI短剧素材包.zip",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 456789,
    "downloaded_at": "2026-10-12T21:02:00+08:00",
    "attachment_token": "boxcnAttachmentToken"
  }
}
```

项目根 `飞书交付回执.json` 使用相同的 `delivery_evidence_version`、hash profile、`segment_readback`、`voice_playback_verification` 与 `visual_preview_evidence`，并引用上面的证据文件。其关键结构如下：

```json
{
  "delivery_evidence_version": 2,
  "prompt_hash_profile": "sha256:utf8-nfc-lf-strip-v1",
  "platform": "feishu_doc",
  "status": "verified",
  "document_url": "https://example.feishu.cn/docx/DocToken123",
  "document_id": "DocToken123",
  "verified_at": "2026-10-12T21:05:00+08:00",
  "readback_evidence_id": "request_20261012_210000",
  "revision": "42",
  "readback_evidence": {
    "filename": "交付证据/飞书回读证据.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 1234
  },
  "previewed_image_ids": ["IMG-C1", "IMG-S1"],
  "previewed_voice_ids": ["V-C1"],
  "segment_readback": {
    "SEG-01": {
      "row_id": "row_segment_01",
      "prompt_block_id": "blk_prompt_seg01",
      "prompt_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "duration_seconds": 10,
      "aspect_ratio": "9:16",
      "image_upload_order": ["IMG-C1", "IMG-S1"],
      "voice_upload_order": ["V-C1"]
    }
  },
  "voice_playback_verification": {
    "V-C1": {
      "status": "played",
      "block_id": "blk_voice_c1",
      "media_token": "boxcnVoiceToken",
      "verified_at": "2026-10-12T20:55:00+08:00",
      "duration_seconds": 8.6,
      "audible": true,
      "correct_character": true,
      "natural": true,
      "no_background_music": true
    }
  },
  "visual_preview_evidence": {
    "filename": "交付证据/飞书视觉预览.pdf",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 123456,
    "captured_at": "2026-10-12T21:00:00+08:00",
    "document_id": "DocToken123",
    "revision": "42",
    "coverage": [
      "document_header", "archive", "character:C1", "scene:S1",
      "image:IMG-C1", "image:IMG-S1", "voice:V-C1", "segment:SEG-01"
    ]
  },
  "current_archive": {
    "filename": "第一集-AI短剧素材包.zip",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 456789,
    "file_count": 15,
    "attachment_token": "boxcnAttachmentToken",
    "cloud_size_bytes": 456789,
    "cloud_download": {
      "filename": "交付证据/云端回下载/第一集-AI短剧素材包.zip",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "size_bytes": 456789,
      "downloaded_at": "2026-10-12T21:02:00+08:00",
      "attachment_token": "boxcnAttachmentToken"
    }
  },
  "readback": {
    "character_card_count": 1,
    "turnaround_count": 1,
    "identity_image_count": 0,
    "system_ui_count": 0,
    "scene_card_count": 1,
    "scene_anchor_count": 1,
    "scene_variant_count": 0,
    "voice_sample_count": 1,
    "pending_voice_count": 0,
    "segment_row_count": 1,
    "prompt_count": 1,
    "archive_count": 1,
    "archive_filename": "第一集-AI短剧素材包.zip",
    "attachment_token": "boxcnAttachmentToken",
    "cloud_size_bytes": 456789
  }
}
```

回执与证据文件重复这些对象是有意设计：校验器要求两处逐字段完全一致，避免回执只写一句“已核对”。最终门规则：

- `segment_readback` 精确覆盖全部镜头 ID；`row_id` 与远端行绑定一致，`prompt_block_id` 跨镜头唯一。远端提示词按 Unicode NFC、换行转 LF、首尾 `strip()` 后做 UTF-8 SHA-256，必须与 manifest `prompt` 一致；时长、比例、图片与声样上传顺序逐项相同。
- `voice_playback_verification` 精确覆盖全部声样。已就绪声样必须是 `played`，含媒体 token、正数时长、带时区时间，且 `audible`、`correct_character`、`natural`、`no_background_music` 全为 `true`；待补声样必须是 `pending` 并有原因与状态块。
- `visual_preview_evidence` 是真实 PNG、JPEG、WebP 或 PDF，文件哈希和大小匹配；coverage 至少覆盖文档头、附件区，以及每个 character、scene、image、voice、segment ID。
- `current_archive.cloud_download` 指向独立回下载 ZIP，不能与本地源 ZIP 共用路径；文件 hash、大小和附件 token 必须与当前本地 ZIP 及文末附件一致。
- 回执与证据中的版本、hash profile、文档身份、分类计数、块 ID、逐镜头远端值、声样播放、视觉证据和云端回下载信息完全一致。
- 本地源 ZIP 仍须通过 CRC、路径安全、inventory 与 production manifest 一致性检查，并排除回执和所有包外交付证据。

校验器只能验证包外证据链的一致性，不能自行证明取证动作真的来自远端。飞书写入、读回、播放、回下载与视觉预览仍须按 `feishu-delivery.md` 实际执行。

## 校验命令与结果

基础门：

```bash
python3 scripts/validate_drama_package.py \
  07_映射与校验/镜头与素材映射.json \
  --root . \
  --aspect 9:16
```

允许显式待补声样的基础文档门：

```bash
python3 scripts/validate_drama_package.py \
  07_映射与校验/镜头与素材映射.json \
  --root . \
  --aspect 9:16 \
  --allow-pending-voices \
  --delivery-receipt 飞书交付回执.json \
  --require-feishu-delivery
```

`--require-feishu-delivery` 保留对 evidence v1 旧回执的兼容；它不会证明逐镜头远端提示词 hash、声样播放、视觉覆盖或云端 ZIP 回下载。所有新交付使用 hardened 最终门：

```bash
python3 scripts/validate_drama_package.py \
  07_映射与校验/镜头与素材映射.json \
  --root . \
  --aspect 9:16 \
  --allow-pending-voices \
  --delivery-receipt 飞书交付回执.json \
  --require-hardened-feishu-delivery
```

结果字段：

- `passed`：本次请求的校验门通过。
- `ready_for_generation`：所有生成素材和声样已就绪。
- `ready_for_document_delivery`：v2 的文档、图片、实体绑定和声样状态记录完整；显式允许时可含待补声样。
- `ready_for_delivery`：`ready_for_document_delivery` 的兼容别名。
- `delivery_verified`：回执、回读内容和本地源 ZIP 通过对应版本的交付门；evidence v1 也可能为 `true`。
- `delivery_hardened_verified`：evidence v2 的逐镜头远端值、逐声样播放、视觉预览证据与云端回下载 ZIP 全部通过 hardened 门。
- `delivery_evidence_version`：本次读取到的交付证据版本；无回执时为 `null`。
- `delivery_assurance`：`hardened_v2`、`legacy` 或 `none`。
- `delivery_verification_scope: local_readback_evidence_only`：结果只证明本地证据链一致。
- `blocker_types.images`、`voices`、`characters`、`scenes`、`delivery`：按类型列出阻断原因。

## v1 兼容

未写 `schema_version` 或写 `1` 时，校验器保留旧默认目录：`参考图/`、`声样/`、`逐段提示词/`。v1 不强制故事契约、人物、场景、inventory、精确 ID、媒体文件头或飞书最终交付门。

生产 manifest v1 的 `--allow-pending-voices` 行为保持不变：它可让带明确待补声样的基础校验返回成功，但 `ready_for_generation` 仍为 `false`，且 manifest v1 不会得到 `ready_for_document_delivery` 或任何飞书交付验证结果。生产 manifest 的 `schema_version` 与飞书包外证据的 `delivery_evidence_version` 是两套独立版本号；当前 hardened 组合是 manifest v2 + delivery evidence v2。
