"""Shared visual rules for editable controls across the application."""

PRIMARY_SETTINGS_PANE_MIN_WIDTH = 250
PRIMARY_SETTINGS_PANE_DEFAULT_WIDTH = 250
PRIMARY_SETTINGS_PANE_MAX_WIDTH = 340

INPUT_CONTROL_STYLE = """
QComboBox, QSpinBox, QLineEdit {
    background-color: #dce5ef;
    color: #182230;
    border: 1px solid #6f8094;
    border-radius: 6px;
    padding: 3px 28px 3px 8px;
    min-height: 26px;
    selection-background-color: #315fbd;
    selection-color: #ffffff;
}
QSpinBox {
    padding-right: 22px;
}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {
    background-color: #cfdeec;
    border-color: #405b79;
}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {
    background-color: #ffffff;
    border: 2px solid #2457b2;
    padding: 2px 27px 2px 7px;
}
QSpinBox:focus {
    padding-right: 21px;
}
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
    background-color: #f3f4f6;
    color: #9aa1aa;
    border: 1px solid #d4d8de;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #182230;
    border: 1px solid #617389;
    selection-background-color: #dce7ff;
    selection-color: #182230;
    outline: 0;
}
QPushButton[operationRole="primary"] {
    background-color: #315fbd;
    color: #ffffff;
    border: 1px solid #244b99;
    border-radius: 8px;
    font-weight: 700;
    padding: 3px 10px;
    min-height: 24px;
}
QPushButton[operationRole="primary"]:hover {
    background-color: #284fa1;
}
QPushButton[operationRole="primary"]:pressed {
    background-color: #1f3f82;
}
QPushButton[operationRole="primary"]:disabled {
    background-color: #d9dee7;
    color: #8a94a3;
    border-color: #c8ced8;
}
QPushButton[operationRole="secondary"] {
    background-color: #f8fafc;
    color: #344054;
    border: 1px solid #aeb9c7;
    border-radius: 7px;
    min-height: 24px;
    padding: 3px 8px;
}
QPushButton[operationRole="secondary"]:hover {
    background-color: #edf2f7;
    border-color: #66788c;
}
QPushButton[operationRole="secondary"]:disabled {
    background-color: #e4e7eb;
    color: #475467;
    border-color: #98a2b3;
}
QWidget[operationRole="saveResult"] {
    background-color: #edf7ed;
    border: 1px solid #a8d5ad;
    border-radius: 8px;
}
"""


def set_operation_role(widget, role: str) -> None:
    """Apply a shared visual role without coupling page modules together."""

    widget.setProperty("operationRole", role)
