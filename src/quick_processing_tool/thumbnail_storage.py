from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .thumbnail_models import (
    CANVAS_PRESETS,
    CanvasPreset,
    FixedLabelSettings,
    NumberingSettings,
    OverlayPosition,
    TextAlignment,
    ThumbnailFormat,
    ThumbnailSettings,
    ThumbnailTemplate,
    VerticalAlignment,
)


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 1
DEFAULT_TEMPLATE_ID = "builtin:simple-dark"
INVALID_DISPLAY_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
COLOR_VALUE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ThumbnailStorageError(RuntimeError):
    """A template or preset could not be validated or persisted."""


def default_thumbnail_storage_path() -> Path:
    override = os.environ.get("QUICK_PROCESSING_TOOL_DATA_DIR")
    base = (
        Path(override)
        if override
        else Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "QuickProcessingTool"
    )
    return base / "templates" / "user_templates.json"


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ThumbnailStorageError(f"{label}が正しい形式ではありません。")
    return value


def _string(value: Any, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ThumbnailStorageError(f"{label}が文字列ではありません。")
    result = value.strip() if not allow_empty else value
    if not allow_empty and not result:
        raise ThumbnailStorageError(f"{label}を入力してください。")
    if len(result) > maximum:
        raise ThumbnailStorageError(f"{label}は{maximum}文字以内にしてください。")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ThumbnailStorageError(f"{label}がtrue/falseではありません。")
    return value

def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ThumbnailStorageError(f"{label}が整数ではありません。")
    if not minimum <= value <= maximum:
        raise ThumbnailStorageError(
            f"{label}は{minimum}〜{maximum}の範囲にしてください。"
        )
    return value


def _color(value: Any, label: str) -> str:
    if not isinstance(value, str) or not COLOR_VALUE.fullmatch(value):
        raise ThumbnailStorageError(f"{label}が正しい色ではありません。")
    return value.upper()


def validate_display_name(name: str) -> str:
    result = _string(name, "名前", 60)
    if INVALID_DISPLAY_NAME.search(result):
        raise ThumbnailStorageError(
            '名前に < > : " / \\ | ? * は使用できません。'
        )
    if result.endswith((" ", ".")):
        raise ThumbnailStorageError("名前の末尾に空白やピリオドは使用できません。")
    return result


def settings_to_dict(settings: ThumbnailSettings) -> dict[str, Any]:
    return {
        "canvas": {
            "preset_name": settings.canvas_preset_name,
            "width": settings.width,
            "height": settings.height,
        },
        "background": {"color": settings.background_color.upper()},
        "title": {
            "font_family": settings.font_family,
            "font_size": settings.font_size,
            "min_font_size": settings.min_font_size,
            "color": settings.font_color.upper(),
            "bold": settings.bold,
            "alignment": settings.alignment.value,
            "vertical_alignment": settings.vertical_alignment.value,
            "margin": settings.margin,
            "max_lines": settings.max_lines,
            "line_spacing": settings.line_spacing,
        },
        "labels": [
            {
                "enabled": label.enabled,
                "text": label.text,
                "font_family": label.font_family,
                "font_size": label.font_size,
                "color": label.color.upper(),
                "position": label.position.value,
            }
            for label in settings.labels
        ],
        "numbering": {
            "enabled": settings.numbering.enabled,
            "prefix": settings.numbering.prefix,
            "start_number": settings.numbering.start_number,
            "digits": settings.numbering.digits,
            "font_family": settings.numbering.font_family,
            "font_size": settings.numbering.font_size,
            "color": settings.numbering.color.upper(),
            "position": settings.numbering.position.value,
        },
        "export": {
            "format": settings.output_format.value,
            "jpeg_quality": settings.quality,
        },
    }


def settings_from_dict(data: Any) -> ThumbnailSettings:
    root = _require_dict(data, "settings")
    canvas = _require_dict(root.get("canvas"), "canvas")
    background = _require_dict(root.get("background"), "background")
    title = _require_dict(root.get("title"), "title")
    numbering = _require_dict(root.get("numbering", {}), "numbering")
    export = _require_dict(root.get("export"), "export")

    labels_data = root.get("labels", [])
    if not isinstance(labels_data, list):
        raise ThumbnailStorageError("labelsが正しい形式ではありません。")
    labels: list[FixedLabelSettings] = []
    for index, raw_label in enumerate(labels_data[:8], start=1):
        label = _require_dict(raw_label, f"label {index}")
        labels.append(
            FixedLabelSettings(
                enabled=_boolean(label.get("enabled", False), "固定ラベルの表示"),
                text=_string(
                    label.get("text", ""),
                    "固定ラベル",
                    200,
                    allow_empty=True,
                ),
                font_family=_string(
                    label.get("font_family", "Yu Gothic UI"),
                    "ラベルのフォント",
                    200,
                ),
                font_size=_integer(label.get("font_size", 28), "ラベルサイズ", 8, 500),
                color=_color(label.get("color", "#FFFFFF"), "ラベル色"),
                position=OverlayPosition(label.get("position", "TopLeft")),
            )
        )
    if not labels:
        labels = [FixedLabelSettings()]

    line_spacing = title.get("line_spacing", 1.15)
    if isinstance(line_spacing, bool) or not isinstance(line_spacing, (int, float)):
        raise ThumbnailStorageError("行間が数値ではありません。")
    line_spacing = float(line_spacing)
    if not 0.8 <= line_spacing <= 3.0:
        raise ThumbnailStorageError("行間が正しい範囲ではありません。")

    return ThumbnailSettings(
        width=_integer(canvas.get("width"), "幅", 128, 6000),
        height=_integer(canvas.get("height"), "高さ", 128, 6000),
        canvas_preset_name=_string(
            canvas.get("preset_name", "任意サイズ"),
            "プリセット名",
            60,
        ),
        background_color=_color(background.get("color"), "背景色"),
        font_family=_string(title.get("font_family"), "タイトルのフォント", 200),
        font_size=_integer(title.get("font_size"), "文字サイズ", 8, 500),
        min_font_size=_integer(
            title.get("min_font_size"), "最小文字サイズ", 8, 500
        ),
        font_color=_color(title.get("color"), "文字色"),
        bold=_boolean(title.get("bold", True), "太字"),
        alignment=TextAlignment(title.get("alignment", "Center")),
        vertical_alignment=VerticalAlignment(
            title.get("vertical_alignment", "Center")
        ),
        margin=_integer(title.get("margin"), "文字領域の余白", 0, 2000),
        max_lines=_integer(title.get("max_lines"), "最大行数", 1, 20),
        line_spacing=line_spacing,
        labels=labels,
        numbering=NumberingSettings(
            enabled=_boolean(numbering.get("enabled", False), "連番の表示"),
            prefix=_string(
                numbering.get("prefix", "#"),
                "連番の接頭辞",
                20,
                allow_empty=True,
            ),
            start_number=_integer(
                numbering.get("start_number", 1),
                "開始番号",
                0,
                99999999,
            ),
            digits=_integer(numbering.get("digits", 3), "桁数", 1, 8),
            font_family=_string(
                numbering.get("font_family", "Yu Gothic UI"),
                "連番のフォント",
                200,
            ),
            font_size=_integer(
                numbering.get("font_size", 28), "連番サイズ", 8, 500
            ),
            color=_color(numbering.get("color", "#FFFFFF"), "連番色"),
            position=OverlayPosition(numbering.get("position", "TopRight")),
        ),
        output_format=ThumbnailFormat(export.get("format")),
        quality=_integer(export.get("jpeg_quality"), "JPEG品質", 1, 100),
    )


def _built_in_templates() -> list[ThumbnailTemplate]:
    dark = ThumbnailSettings()
    white = ThumbnailSettings(
        background_color="#FFFFFF",
        font_color="#111827",
    )
    mincho = ThumbnailSettings(font_family="Yu Mincho")
    return [
        ThumbnailTemplate(SCHEMA_VERSION, DEFAULT_TEMPLATE_ID, "シンプル黒", dark, True),
        ThumbnailTemplate(
            SCHEMA_VERSION,
            "builtin:simple-white",
            "シンプル白",
            white,
            True,
        ),
        ThumbnailTemplate(
            SCHEMA_VERSION,
            "builtin:mincho-dark",
            "明朝ダーク",
            mincho,
            True,
        ),
    ]


class ThumbnailTemplateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_thumbnail_storage_path()
        self._user_templates: list[ThumbnailTemplate] = []
        self._user_presets: list[CanvasPreset] = []
        self._last_selected_template = DEFAULT_TEMPLATE_ID
        self._last_output_folder = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            root = json.loads(self.path.read_text(encoding="utf-8"))
            root = _require_dict(root, "template storage")
            version = root.get("schema_version")
            if version != SCHEMA_VERSION:
                raise ThumbnailStorageError(
                    f"未対応のschema_versionです: {version}"
                )
            templates: list[ThumbnailTemplate] = []
            for raw in root.get("user_templates", []):
                try:
                    item = _require_dict(raw, "template")
                    if item.get("schema_version") != SCHEMA_VERSION:
                        raise ThumbnailStorageError("template schema_versionが未対応です。")
                    template_id = _string(item.get("template_id"), "template_id", 100)
                    if not template_id.startswith("user:"):
                        raise ThumbnailStorageError("user template IDが不正です。")
                    templates.append(
                        ThumbnailTemplate(
                            SCHEMA_VERSION,
                            template_id,
                            validate_display_name(item.get("name", "")),
                            settings_from_dict(item.get("settings")),
                            False,
                        )
                    )
                except (ThumbnailStorageError, ValueError) as exc:
                    LOGGER.warning("Invalid thumbnail template skipped: %s", exc)
            presets: list[CanvasPreset] = []
            for raw in root.get("size_presets", []):
                try:
                    item = _require_dict(raw, "size preset")
                    presets.append(
                        CanvasPreset(
                            validate_display_name(item.get("name", "")),
                            _integer(item.get("width"), "幅", 128, 6000),
                            _integer(item.get("height"), "高さ", 128, 6000),
                        )
                    )
                except ThumbnailStorageError as exc:
                    LOGGER.warning("Invalid canvas preset skipped: %s", exc)
            self._user_templates = self._dedupe_templates(templates)
            self._user_presets = self._dedupe_presets(presets)
            self._last_selected_template = str(
                root.get("last_selected_template", DEFAULT_TEMPLATE_ID)
            )
            self._last_output_folder = str(root.get("last_output_folder", ""))
        except (OSError, json.JSONDecodeError, ThumbnailStorageError, TypeError) as exc:
            LOGGER.warning("Thumbnail storage ignored (%s): %s", self.path, exc)
            self._user_templates = []
            self._user_presets = []
            self._last_selected_template = DEFAULT_TEMPLATE_ID
            self._last_output_folder = ""

    @staticmethod
    def _dedupe_templates(items: list[ThumbnailTemplate]) -> list[ThumbnailTemplate]:
        result: list[ThumbnailTemplate] = []
        names = {item.name.casefold() for item in _built_in_templates()}
        ids: set[str] = set()
        for item in items:
            key = item.name.casefold()
            if key in names or item.template_id in ids:
                LOGGER.warning("Duplicate thumbnail template skipped: %s", item.name)
                continue
            names.add(key)
            ids.add(item.template_id)
            result.append(item)
        return result

    @staticmethod
    def _dedupe_presets(items: list[CanvasPreset]) -> list[CanvasPreset]:
        result: list[CanvasPreset] = []
        names = {item.name.casefold() for item in CANVAS_PRESETS}
        for item in items:
            key = item.name.casefold()
            if key in names:
                LOGGER.warning("Duplicate canvas preset skipped: %s", item.name)
                continue
            names.add(key)
            result.append(item)
        return result

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "last_selected_template": self.last_selected_template,
            "last_output_folder": self._last_output_folder,
            "user_templates": [
                {
                    "schema_version": SCHEMA_VERSION,
                    "template_id": item.template_id,
                    "name": item.name,
                    "settings": settings_to_dict(item.settings),
                }
                for item in self._user_templates
            ],
            "size_presets": [
                {"name": item.name, "width": item.width, "height": item.height}
                for item in self._user_presets
            ],
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(self._payload(), output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise ThumbnailStorageError(f"テンプレートを保存できません: {exc}") from exc

    def templates(self) -> list[ThumbnailTemplate]:
        return copy.deepcopy(_built_in_templates() + self._user_templates)

    def get_template(self, template_id: str) -> ThumbnailTemplate | None:
        return next(
            (
                copy.deepcopy(item)
                for item in self.templates()
                if item.template_id == template_id
            ),
            None,
        )

    @property
    def last_selected_template(self) -> str:
        available = {item.template_id for item in self.templates()}
        if self._last_selected_template in available:
            return self._last_selected_template
        return DEFAULT_TEMPLATE_ID

    @property
    def last_output_folder(self) -> str:
        return self._last_output_folder

    def create_template(self, name: str, settings: ThumbnailSettings) -> ThumbnailTemplate:
        checked = validate_display_name(name)
        self._ensure_unique_template_name(checked)
        template = ThumbnailTemplate(
            SCHEMA_VERSION,
            f"user:{uuid.uuid4().hex}",
            checked,
            copy.deepcopy(settings),
            False,
        )
        previous_selected = self._last_selected_template
        self._user_templates.append(template)
        self._last_selected_template = template.template_id
        try:
            self._write()
        except ThumbnailStorageError:
            self._user_templates.pop()
            self._last_selected_template = previous_selected
            raise
        return copy.deepcopy(template)

    def update_template(self, template_id: str, settings: ThumbnailSettings) -> ThumbnailTemplate:
        for index, item in enumerate(self._user_templates):
            if item.template_id == template_id:
                updated = ThumbnailTemplate(
                    SCHEMA_VERSION,
                    item.template_id,
                    item.name,
                    copy.deepcopy(settings),
                    False,
                )
                previous = self._user_templates[index]
                self._user_templates[index] = updated
                try:
                    self._write()
                except ThumbnailStorageError:
                    self._user_templates[index] = previous
                    raise
                return copy.deepcopy(updated)
        raise ThumbnailStorageError("標準テンプレートは直接更新できません。")

    def delete_template(self, template_id: str) -> None:
        for index, item in enumerate(self._user_templates):
            if item.template_id == template_id:
                removed = self._user_templates.pop(index)
                previous_selected = self._last_selected_template
                if previous_selected == template_id:
                    self._last_selected_template = DEFAULT_TEMPLATE_ID
                try:
                    self._write()
                except ThumbnailStorageError:
                    self._user_templates.insert(index, removed)
                    self._last_selected_template = previous_selected
                    raise
                return
        raise ThumbnailStorageError("標準テンプレートは削除できません。")

    def _ensure_unique_template_name(self, name: str) -> None:
        if name.casefold() in {item.name.casefold() for item in self.templates()}:
            raise ThumbnailStorageError("同じ名前のテンプレートが既にあります。")

    def set_last_selected(self, template_id: str) -> None:
        if self.get_template(template_id) is None:
            template_id = DEFAULT_TEMPLATE_ID
        previous = self._last_selected_template
        self._last_selected_template = template_id
        try:
            self._write()
        except ThumbnailStorageError:
            self._last_selected_template = previous
            raise

    def set_last_output_folder(self, folder: Path) -> None:
        previous = self._last_output_folder
        self._last_output_folder = str(folder)
        try:
            self._write()
        except ThumbnailStorageError:
            self._last_output_folder = previous
            raise

    def canvas_presets(self) -> list[CanvasPreset]:
        return list(CANVAS_PRESETS) + copy.deepcopy(self._user_presets)

    def add_canvas_preset(self, name: str, width: int, height: int) -> CanvasPreset:
        checked = validate_display_name(name)
        width = _integer(width, "幅", 128, 6000)
        height = _integer(height, "高さ", 128, 6000)
        if checked.casefold() in {
            item.name.casefold() for item in self.canvas_presets()
        }:
            raise ThumbnailStorageError("同じ名前のサイズPresetが既にあります。")
        preset = CanvasPreset(checked, width, height)
        self._user_presets.append(preset)
        try:
            self._write()
        except ThumbnailStorageError:
            self._user_presets.pop()
            raise
        return preset

    def delete_canvas_preset(self, name: str) -> None:
        for index, item in enumerate(self._user_presets):
            if item.name == name:
                removed = self._user_presets.pop(index)
                try:
                    self._write()
                except ThumbnailStorageError:
                    self._user_presets.insert(index, removed)
                    raise
                return
        raise ThumbnailStorageError("標準サイズPresetは削除できません。")

    def is_user_preset(self, name: str) -> bool:
        return any(item.name == name for item in self._user_presets)