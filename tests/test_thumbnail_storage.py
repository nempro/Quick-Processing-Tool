from __future__ import annotations

import json
from pathlib import Path

import pytest

from quick_processing_tool.thumbnail_models import (
    FixedLabelSettings,
    NumberingSettings,
    OverlayPosition,
    ThumbnailFormat,
    ThumbnailSettings,
)
from quick_processing_tool.thumbnail_storage import (
    DEFAULT_TEMPLATE_ID,
    SCHEMA_VERSION,
    ThumbnailStorageError,
    ThumbnailTemplateStore,
)


def configured_settings() -> ThumbnailSettings:
    return ThumbnailSettings(
        width=1200,
        height=630,
        canvas_preset_name="Custom Cover",
        background_color="#221144",
        font_family="Yu Mincho",
        font_size=68,
        min_font_size=30,
        font_color="#FFFEEE",
        labels=[
            FixedLabelSettings(
                True,
                "過去音声",
                "Yu Gothic UI",
                30,
                "#FFCC00",
                OverlayPosition.TOP_LEFT,
            ),
            FixedLabelSettings(
                False,
                "将来用ラベル",
                "Yu Gothic UI",
                24,
                "#FFFFFF",
                OverlayPosition.BOTTOM_LEFT,
            ),
        ],
        numbering=NumberingSettings(
            True,
            "#",
            50,
            3,
            "Yu Gothic UI",
            28,
            "#66CCFF",
            OverlayPosition.TOP_RIGHT,
        ),
        output_format=ThumbnailFormat.PNG,
        quality=88,
    )


def test_built_in_templates_exist_without_storage_file(tmp_path: Path) -> None:
    store = ThumbnailTemplateStore(tmp_path / "templates.json")
    templates = store.templates()
    assert [item.name for item in templates] == [
        "シンプル黒",
        "シンプル白",
        "明朝ダーク",
    ]
    assert all(item.built_in for item in templates)
    assert store.last_selected_template == DEFAULT_TEMPLATE_ID


def test_template_round_trip_has_schema_and_never_contains_titles(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    store = ThumbnailTemplateStore(path)
    created = store.create_template("Fantia Archive", configured_settings())

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["user_templates"][0]["schema_version"] == SCHEMA_VERSION
    assert "夜中寝てたら" not in path.read_text(encoding="utf-8")

    restored_store = ThumbnailTemplateStore(path)
    restored = restored_store.get_template(created.template_id)
    assert restored is not None
    assert restored.name == "Fantia Archive"
    assert restored.settings.width == 1200
    assert restored.settings.height == 630
    assert restored.settings.fixed_label.text == "過去音声"
    assert len(restored.settings.labels) == 2
    assert restored.settings.labels[1].text == "将来用ラベル"
    assert restored.settings.numbering.text_for_index(2) == "#051"
    assert restored.settings.output_format is ThumbnailFormat.PNG
    assert restored_store.last_selected_template == created.template_id


def test_template_update_save_as_delete_and_same_name_handling(tmp_path: Path) -> None:
    store = ThumbnailTemplateStore(tmp_path / "templates.json")
    first = store.create_template("Fantia Archive", configured_settings())
    changed = configured_settings()
    changed.background_color = "#123456"
    updated = store.update_template(first.template_id, changed)
    assert updated.template_id == first.template_id
    assert store.get_template(first.template_id).settings.background_color == "#123456"

    second = store.create_template("Fantia 無料公開", changed)
    assert second.template_id != first.template_id
    assert store.get_template(first.template_id).name == "Fantia Archive"

    with pytest.raises(ThumbnailStorageError, match="同じ名前"):
        store.create_template("fantia archive", changed)
    with pytest.raises(ThumbnailStorageError, match="使用できません"):
        store.create_template("Fantia:Archive", changed)
    with pytest.raises(ThumbnailStorageError, match="直接更新"):
        store.update_template(DEFAULT_TEMPLATE_ID, changed)

    store.delete_template(first.template_id)
    assert store.get_template(first.template_id) is None
    with pytest.raises(ThumbnailStorageError, match="削除できません"):
        store.delete_template(DEFAULT_TEMPLATE_ID)


def test_deleted_last_template_falls_back_safely(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    store = ThumbnailTemplateStore(path)
    created = store.create_template("一時テンプレート", configured_settings())
    assert store.last_selected_template == created.template_id
    store.delete_template(created.template_id)
    assert store.last_selected_template == DEFAULT_TEMPLATE_ID
    assert ThumbnailTemplateStore(path).last_selected_template == DEFAULT_TEMPLATE_ID


def test_corrupt_unknown_and_invalid_template_data_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    path.write_text("{broken", encoding="utf-8")
    assert len(ThumbnailTemplateStore(path).templates()) == 3

    path.write_text(
        json.dumps({"schema_version": 99, "user_templates": []}),
        encoding="utf-8",
    )
    assert len(ThumbnailTemplateStore(path).templates()) == 3

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "user_templates": [
                    {
                        "schema_version": 1,
                        "template_id": "user:broken",
                        "name": "壊れた設定",
                        "settings": {"canvas": {"width": -1}},
                    }
                ],
                "size_presets": [],
            }
        ),
        encoding="utf-8",
    )
    assert len(ThumbnailTemplateStore(path).templates()) == 3


def test_last_selected_and_output_folder_restore_without_titles(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    output = tmp_path / "outputs"
    output.mkdir()
    store = ThumbnailTemplateStore(path)
    created = store.create_template("再起動確認", configured_settings())
    store.set_last_selected(created.template_id)
    store.set_last_output_folder(output)

    restored = ThumbnailTemplateStore(path)
    assert restored.last_selected_template == created.template_id
    assert restored.last_output_folder == str(output)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "titles" not in payload


def test_user_canvas_preset_add_load_delete_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    store = ThumbnailTemplateStore(path)
    created = store.add_canvas_preset("Custom Cover", 1200, 630)
    assert created.width == 1200
    assert ThumbnailTemplateStore(path).canvas_presets()[-1] == created

    with pytest.raises(ThumbnailStorageError, match="同じ名前"):
        store.add_canvas_preset("custom cover", 1200, 630)
    with pytest.raises(ThumbnailStorageError, match="128"):
        store.add_canvas_preset("Too Small", 10, 630)

    store.delete_canvas_preset("Custom Cover")
    assert all(item.name != "Custom Cover" for item in store.canvas_presets())
    with pytest.raises(ThumbnailStorageError, match="標準"):
        store.delete_canvas_preset("1:1")


def test_atomic_write_failure_rolls_back_memory_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "templates.json"
    store = ThumbnailTemplateStore(path)
    existing = store.create_template("保存済み", configured_settings())
    original = path.read_bytes()

    def fail_replace(source, destination) -> None:
        del source, destination
        raise OSError("blocked")

    monkeypatch.setattr("quick_processing_tool.thumbnail_storage.os.replace", fail_replace)
    with pytest.raises(ThumbnailStorageError, match="保存できません"):
        store.create_template("保存失敗", configured_settings())
    assert path.read_bytes() == original
    assert store.get_template(existing.template_id) is not None
    assert all(item.name != "保存失敗" for item in store.templates())

@pytest.mark.parametrize(
    "field_path",
    [
        ("title", "bold"),
        ("labels", 0, "enabled"),
        ("numbering", "enabled"),
    ],
)
def test_non_boolean_template_values_are_rejected(
    tmp_path: Path,
    field_path: tuple[object, ...],
) -> None:
    path = tmp_path / "templates.json"
    store = ThumbnailTemplateStore(path)
    store.create_template("Boolean確認", configured_settings())
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = payload["user_templates"][0]["settings"]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = "false"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    restored = ThumbnailTemplateStore(path)
    assert len(restored.templates()) == 3
