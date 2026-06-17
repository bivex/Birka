DARK_STYLESHEET = """
/* Global styles */
QWidget {
    background-color: #121214;
    color: #e3e3e6;
    font-family: "SF Pro Display", "Inter", "Segoe UI", sans-serif;
    font-size: 13px;
}

/* Scrollbars */
QScrollBar:vertical {
    border: none;
    background: #121214;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #3a3a45;
    min-height: 20px;
    border-radius: 5px;
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
    background: #121214;
    height: 10px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: #3a3a45;
    min-width: 20px;
    border-radius: 5px;
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
    border: 1px solid #1a1a22;
    background-color: #121214;
    border-radius: 8px;
    padding: 10px;
}
QTabBar::tab {
    background-color: #1a1a22;
    border: 1px solid #252530;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 16px;
    margin-right: 2px;
    color: #a0a0ab;
    font-weight: bold;
}
QTabBar::tab:selected {
    background-color: #121214;
    color: #00f0ff;
    border: 1px solid #1a1a22;
    border-bottom: 2px solid #00f0ff;
}
QTabBar::tab:hover {
    color: #ffffff;
    background-color: #20202b;
}

/* Table View styling */
QTableView {
    background-color: #1a1a22;
    border: 1px solid #2a2a35;
    gridline-color: #252530;
    border-radius: 6px;
    selection-background-color: #2b2b3a;
    selection-color: #00f0ff;
    padding: 5px;
}
QTableView::item {
    padding: 6px;
    border-bottom: 1px solid #252530;
}
QTableView::item:selected {
    background-color: #2d2d3d;
    color: #00f0ff;
    border-left: 3px solid #00f0ff;
    font-weight: bold;
}
QTableView::item:hover {
    background-color: #252533;
}
QHeaderView::section {
    background-color: #1e1e28;
    color: #a0a0ab;
    padding: 6px;
    border: none;
    border-right: 1px solid #252530;
    border-bottom: 2px solid #252530;
    font-weight: bold;
}
QHeaderView::section:last {
    border-right: none;
}

/* Form Elements: Input fields, Combo boxes, Spin boxes */
QLineEdit, QSpinBox, QComboBox {
    background-color: #1a1a22;
    border: 1px solid #2d2d3d;
    border-radius: 5px;
    padding: 6px 10px;
    color: #e3e3e6;
    selection-background-color: #00f0ff;
    selection-color: #121214;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #00f0ff;
    background-color: #1e1e2a;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid #a0a0ab;
    width: 0;
    height: 0;
    margin-right: 8px;
}
QComboBox::down-arrow:hover {
    border-top-color: #00f0ff;
}

/* Buttons styling */
QPushButton {
    background-color: #22222f;
    border: 1px solid #2d2d3d;
    border-radius: 5px;
    padding: 6px 12px;
    color: #e3e3e6;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #2d2d3f;
    border-color: #00f0ff;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #1a1a28;
}
QPushButton:disabled {
    background-color: #15151c;
    border-color: #1c1c24;
    color: #555562;
}

/* Specific highlight buttons */
QPushButton#playButton {
    background-color: #0f3d32;
    border: 1px solid #1ba87d;
    color: #2ecc71;
}
QPushButton#playButton:hover {
    background-color: #175445;
    color: #39e385;
    border-color: #39e385;
}
QPushButton#stopButton {
    background-color: #3d1c1c;
    border: 1px solid #a83b3b;
    color: #e74c3c;
}
QPushButton#stopButton:hover {
    background-color: #542525;
    color: #ff5c5c;
    border-color: #ff5c5c;
}
QPushButton#deleteButton {
    background-color: #3d1c1c;
    border: 1px solid #a83b3b;
    color: #e74c3c;
}
QPushButton#deleteButton:hover {
    background-color: #542525;
    color: #ff5c5c;
    border-color: #ff5c5c;
}

/* Slider styling */
QSlider::groove:horizontal {
    border: none;
    height: 6px;
    background: #22222f;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #00f0ff;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 1px solid #2d2d3d;
    width: 14px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: #00f0ff;
    border-color: #ffffff;
}

/* Group box or sections styling */
QFrame#containerFrame {
    background-color: #15151c;
    border: 1px solid #20202b;
    border-radius: 8px;
    padding: 10px;
}
"""
