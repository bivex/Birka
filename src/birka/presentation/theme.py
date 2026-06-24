DARK_STYLESHEET = """
/* Global styles */
QWidget {
    background-color: #0c0c0e;
    color: #e3e3e8;
    font-family: -apple-system, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 13px;
}

/* Scrollbars */
QScrollBar:vertical {
    border: none;
    background: #0c0c0e;
    width: 8px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #2a2a35;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #00f0ff;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    border: none;
    background: #0c0c0e;
    height: 8px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: #2a2a35;
    min-width: 20px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: #00f0ff;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* Main window background */
QMainWindow {
    background-color: #0c0c0e;
}

/* Tabs styling */
QTabWidget::pane {
    border: 1px solid #252535;
    background-color: #121216;
    border-radius: 8px;
    padding: 12px;
}
QTabBar::tab {
    background-color: #181822;
    border: 1px solid #252535;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 18px;
    margin-right: 4px;
    color: #8a8a9a;
    font-weight: bold;
}
QTabBar::tab:selected {
    background-color: #121216;
    color: #00f0ff;
    border: 1px solid #252535;
    border-bottom: 2px solid #00f0ff;
}
QTabBar::tab:hover {
    color: #ffffff;
    background-color: #222232;
}

/* Table View styling */
QTableView {
    background-color: #161620;
    border: 1px solid #252535;
    gridline-color: #20202d;
    border-radius: 6px;
    selection-background-color: #28283a;
    selection-color: #00f0ff;
    padding: 4px;
}
QTableView::item {
    padding: 6px;
    border-bottom: 1px solid #20202d;
}
QTableView::item:selected {
    background-color: #2c2c3e;
    color: #00f0ff;
    font-weight: bold;
}
QTableView::item:hover {
    background-color: #202030;
}
QHeaderView::section {
    background-color: #1a1a26;
    color: #a0a0b0;
    padding: 6px;
    border: none;
    border-right: 1px solid #20202d;
    border-bottom: 2px solid #252535;
    font-weight: bold;
}
QHeaderView::section:last {
    border-right: none;
}

/* Form Elements: Input fields, Combo boxes, Spin boxes */
QLineEdit, QSpinBox, QComboBox {
    background-color: #161622;
    border: 1px solid #28283a;
    border-radius: 6px;
    padding: 6px 12px;
    color: #e3e3e8;
    selection-background-color: #00f0ff;
    selection-color: #0c0c0e;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #00f0ff;
    background-color: #1a1a2a;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 4px solid #a0a0b0;
    width: 0;
    height: 0;
    margin-right: 10px;
}
QComboBox::down-arrow:hover {
    border-top-color: #00f0ff;
}

/* General button styling */
QPushButton {
    background-color: #1d1d29;
    border: 1px solid #2d2d3e;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e3e3e8;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #252538;
    border-color: #00f0ff;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #151522;
}
QPushButton:disabled {
    background-color: #121218;
    border-color: #1a1a24;
    color: #555562;
}

/* Specific highlight buttons */

/* Play Button: Glowing Emerald */
QPushButton#playButton {
    background-color: #08281d;
    border: 1px solid #00ffaa;
    color: #00ffaa;
}
QPushButton#playButton:hover {
    background-color: #0c3e2e;
    color: #33ffcc;
    border-color: #33ffcc;
}
QPushButton#playButton:pressed {
    background-color: #051a13;
}

/* Play Fast Button: Glowing Amber (quick low-quality preview) */
QPushButton#playFastButton {
    background-color: #2a1c05;
    border: 1px solid #ffaa00;
    color: #ffaa00;
}
QPushButton#playFastButton:hover {
    background-color: #3c2807;
    color: #ffc44d;
    border-color: #ffc44d;
}
QPushButton#playFastButton:pressed {
    background-color: #1c1203;
}

/* Stop / Delete Button: Glowing Crimson */
QPushButton#stopButton, QPushButton#deleteButton {
    background-color: #301414;
    border: 1px solid #ff3b30;
    color: #ff3b30;
}
QPushButton#stopButton:hover, QPushButton#deleteButton:hover {
    background-color: #481b1b;
    color: #ff6961;
    border-color: #ff6961;
}
QPushButton#stopButton:pressed, QPushButton#deleteButton:pressed {
    background-color: #200d0d;
}

/* Sort Button: Glowing Gold */
QPushButton#sortButton {
    background-color: #2d2205;
    border: 1px solid #ffcc00;
    color: #ffcc00;
}
QPushButton#sortButton:hover {
    background-color: #423207;
    color: #ffe066;
    border-color: #ffe066;
}
QPushButton#sortButton:pressed {
    background-color: #1d1603;
}

/* Render MIDI Button: Glowing Hot Pink */
QPushButton#renderButton {
    background-color: #300517;
    border: 1px solid #ff007f;
    color: #ff007f;
}
QPushButton#renderButton:hover {
    background-color: #470723;
    color: #ff4da6;
    border-color: #ff4da6;
}
QPushButton#renderButton:pressed {
    background-color: #20030f;
}

/* Pager Buttons: Cyan outline */
QPushButton#prevButton, QPushButton#nextButton {
    background-color: #161622;
    border: 1px solid #28283a;
    color: #a0a0b0;
}
QPushButton#prevButton:hover, QPushButton#nextButton:hover {
    border-color: #00f0ff;
    color: #00f0ff;
    background-color: #1a1a2a;
}

/* Sliders */
QSlider::groove:horizontal {
    border: none;
    height: 6px;
    background: #1c1c28;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #00f0ff;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 1px solid #2d2d3e;
    width: 12px;
    margin-top: -3px;
    margin-bottom: -3px;
    border-radius: 6px;
}
QSlider::handle:horizontal:hover {
    background: #00f0ff;
    border-color: #ffffff;
}

/* Group box or sections styling */
QFrame#containerFrame {
    background-color: #121218;
    border: 1px solid #222232;
    border-radius: 10px;
    padding: 12px;
}
"""
