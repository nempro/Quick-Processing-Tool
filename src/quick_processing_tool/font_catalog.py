from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QFontDatabase


_PREFERRED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Yu Gothic UI", "游ゴシック UI", "游ゴシックUI"),
    ("Yu Gothic", "游ゴシック"),
    ("Yu Mincho", "游明朝"),
    ("Meiryo", "メイリオ"),
    ("MS Gothic", "ＭＳ ゴシック", "MS ゴシック"),
    ("MS Mincho", "ＭＳ 明朝", "MS 明朝"),
)
SUPPORTED_FONT_SUFFIXES = {".ttf", ".otf"}


@dataclass(frozen=True)
class FontRegistration:
    path: Path
    families: tuple[str, ...] = ()
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return bool(self.families) and not self.error


class FontCatalog:
    """Installed/application font view with deliberately non-persistent registration."""

    def __init__(self) -> None:
        self._registrations: dict[Path, FontRegistration] = {}

    def families(self) -> tuple[str, ...]:
        return tuple(sorted(QFontDatabase.families(), key=str.casefold))

    def preferred_families(self) -> tuple[str, ...]:
        installed = {family.casefold(): family for family in self.families()}
        selected: list[str] = []
        for aliases in _PREFERRED_GROUPS:
            for alias in aliases:
                actual = installed.get(alias.casefold())
                if actual is not None:
                    selected.append(actual)
                    break
        return tuple(selected)

    def default_family(self) -> str:
        preferred = self.preferred_families()
        if preferred:
            return preferred[0]
        families = self.families()
        return families[0] if families else "Sans Serif"

    def resolve_family(self, requested: str) -> str:
        installed = {family.casefold(): family for family in self.families()}
        return installed.get(requested.strip().casefold(), self.default_family())

    def register_file(self, path: Path) -> FontRegistration:
        path = Path(path)
        key = path.resolve()
        if key in self._registrations:
            return self._registrations[key]
        if path.suffix.casefold() not in SUPPORTED_FONT_SUFFIXES:
            return FontRegistration(path, error="TTF または OTF ファイルを選んでください。")
        if not path.is_file():
            return FontRegistration(path, error="フォントファイルを読み込めませんでした。")
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            return FontRegistration(path, error="フォントファイルを読み込めませんでした。")
        families = tuple(QFontDatabase.applicationFontFamilies(font_id))
        if not families:
            return FontRegistration(path, error="フォント名を取得できませんでした。")
        result = FontRegistration(path, families=families)
        self._registrations[key] = result
        return result
