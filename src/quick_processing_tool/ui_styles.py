"""Shared visual rules for editable controls across the application."""

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
"""
