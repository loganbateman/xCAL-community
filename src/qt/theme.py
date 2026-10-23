"""
Qt stylesheet (QSS) for xCAL — Excalibur Racing gray + ruby theme.

Reuses the palette from src/config.py so the Qt UI matches the brand exactly.
Qt renders each page to an offscreen buffer and blits it in one go, so pages
appear fully composed — no widget-by-widget "tearing".
"""
from __future__ import annotations
import config

# Re-export palette so views don't reach into config directly.
BG        = config.COLOR_BG_DARK      # main content background
SIDEBAR   = config.COLOR_SIDEBAR      # nav rail
ACCENT    = config.COLOR_ACCENT       # ruby
ACCENT_HI = config.COLOR_ACCENT_HOVER
TEXT      = config.COLOR_TEXT
MUTED     = config.COLOR_TEXT_MUTED
SUCCESS   = config.COLOR_SUCCESS
WARNING   = config.COLOR_WARNING
CARD      = config.COLOR_CARD
CARD_HI   = config.COLOR_CARD_HOVER
BORDER    = config.COLOR_BORDER


def stylesheet() -> str:
    chevron = str(config.ASSETS_DIR / "icon_chevron.svg").replace("\\", "/")
    return f"""
    QWidget {{
        background-color: {BG};
        color: {TEXT};
        font-family: "Segoe UI";
        font-size: 13px;
    }}

    /* Labels are transparent by default so they blend into their container
       (cards, sidebar, hero) instead of painting the page color behind text.
       Cells that need a fill set their own background inline. */
    QLabel {{ background: transparent; }}

    /* Sidebar */
    #Sidebar {{
        background-color: {SIDEBAR};
        border: none;
    }}
    #Sidebar QLabel#Version {{ color: {MUTED}; font-size: 11px; }}
    QPushButton#NavButton {{
        background-color: transparent;
        color: {MUTED};
        border: none;
        border-radius: 8px;
        padding: 10px 14px;
        text-align: left;
        font-size: 14px;
    }}
    QPushButton#NavButton:hover {{ background-color: {CARD_HI}; color: {TEXT}; }}
    QPushButton#NavButton:checked {{ background-color: {ACCENT}; color: white; }}

    /* Cards */
    QFrame#Card {{
        background-color: {CARD};
        border: 1px solid {BORDER};
        border-radius: 10px;
    }}
    QFrame#Card:hover {{ background-color: {CARD}; }}
    QFrame#Hero {{
        background-color: {CARD};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}

    QLabel#H1 {{ font-size: 26px; font-weight: 700; }}
    QLabel#H2 {{ font-size: 16px; font-weight: 700; }}
    QLabel#Muted {{ color: {MUTED}; }}
    QLabel#Accent {{ color: {ACCENT}; font-weight: 700; }}
    QLabel#Success {{ color: {SUCCESS}; }}
    QLabel#SeasonHdr {{ font-size: 15px; font-weight: 700; }}
    QLabel#CardTitle {{ font-size: 13px; font-weight: 700; }}
    QLabel#CardSub {{ color: {MUTED}; font-size: 11px; }}

    /* Primary / secondary buttons */
    QPushButton#Primary {{
        background-color: {ACCENT}; color: white;
        border: none; border-radius: 8px; padding: 8px 16px; font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background-color: {ACCENT_HI}; }}
    QPushButton#Ghost {{
        background-color: transparent; color: {MUTED};
        border: 2px solid {BORDER}; border-radius: 8px; padding: 6px 13px;
    }}
    QPushButton#Ghost:hover {{
        background-color: {CARD_HI}; color: {TEXT}; border-color: {MUTED};
    }}
    QPushButton#Ghost:disabled {{
        background-color: transparent; color: #777b85;
        border: 2px solid {BORDER};
    }}
    QPushButton#Danger {{
        background-color: transparent; color: #ff9da5;
        border: 1px solid #7a3038; border-radius: 8px; padding: 7px 12px;
    }}
    QPushButton#Danger:hover {{ background-color: #7a3038; color: white; }}

    /* Inputs */
    QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background-color: {SIDEBAR};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 8px;
        color: {TEXT};
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
    QLineEdit[dirty="true"], QComboBox[dirty="true"],
    QPlainTextEdit[dirty="true"] {{
        background-color: #244a31;
        border: 2px solid {SUCCESS};
        color: #dfffe8;
    }}
    /* Baseline differences remain yellow after saving; green border still
       distinguishes fields that also contain unsaved edits. */
    QLineEdit[baselineChanged="true"], QComboBox[baselineChanged="true"],
    QPlainTextEdit[baselineChanged="true"] {{
        background-color: #493e20;
        border: 2px solid #d9b64d;
        color: #fff1b8;
    }}
    QLineEdit[baselineChanged="true"][dirty="true"],
    QComboBox[baselineChanged="true"][dirty="true"],
    QPlainTextEdit[baselineChanged="true"][dirty="true"] {{
        border: 2px solid {SUCCESS};
    }}
    /* Previous-run differences are red. Preserve yellow fill when both
       comparisons differ; unsaved state remains visible in the save status. */
    QLineEdit[previousRunChanged="true"], QComboBox[previousRunChanged="true"],
    QPlainTextEdit[previousRunChanged="true"] {{
        background-color: #4c252b;
        border: 2px solid #ed7373;
        color: #ffe6e6;
    }}
    QLineEdit[baselineChanged="true"][previousRunChanged="true"],
    QComboBox[baselineChanged="true"][previousRunChanged="true"],
    QPlainTextEdit[baselineChanged="true"][previousRunChanged="true"] {{
        background-color: #493e20;
        border: 2px solid #ed7373;
        color: #fff1b8;
    }}
    QLineEdit[metadataReadOnly="true"],
    QComboBox[metadataReadOnly="true"],
    QPlainTextEdit[metadataReadOnly="true"] {{
        background-color: {SIDEBAR}; border: 1px solid {BORDER};
        color: {TEXT}; padding: 6px 8px; font-weight: 600;
    }}
    QComboBox[metadataReadOnly="true"]::drop-down {{
        width: 0; border: none;
    }}
    QLabel#RevisionSelected {{
        background-color: {ACCENT}; color: white;
        border-radius: 6px; padding: 6px 9px; font-weight: 700;
    }}
    QLabel#ComparisonValue {{
        background-color: {SIDEBAR}; border: 1px solid {BORDER};
        border-radius: 6px; padding: 6px 8px; color: {TEXT};
    }}
    QLabel#ComparisonChanged {{
        background-color: #432a2e; border: 1px solid {ACCENT};
        border-radius: 6px; padding: 6px 8px; color: white;
    }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
    QComboBox QAbstractItemView {{
        background-color: {SIDEBAR}; color: {TEXT};
        selection-background-color: {ACCENT}; border: 1px solid {BORDER};
    }}
    QCalendarWidget {{
        background-color: {CARD}; border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{
        background-color: {SIDEBAR}; padding: 5px;
    }}
    QCalendarWidget QToolButton {{
        background: transparent; color: {TEXT}; border: none;
        border-radius: 5px; padding: 5px 8px; font-weight: 600;
    }}
    QCalendarWidget QToolButton:hover {{ background-color: {CARD_HI}; }}
    QCalendarWidget QToolButton#qt_calendar_monthbutton::menu-indicator {{
        image: none; width: 0px;
    }}
    QCalendarWidget QAbstractItemView {{
        background-color: {CARD}; color: {TEXT};
        selection-background-color: {ACCENT}; selection-color: white;
        outline: none;
    }}

    /* Inventory lists and sortable tables */
    QListWidget, QTableWidget {{
        background-color: {SIDEBAR};
        alternate-background-color: {CARD};
        border: 1px solid {BORDER};
        border-radius: 7px;
        gridline-color: {BORDER};
        selection-background-color: {ACCENT};
        selection-color: white;
        outline: none;
    }}
    QListWidget::item {{ padding: 7px 8px; border-radius: 5px; }}
    QListWidget::item:hover:!selected {{ background-color: {CARD_HI}; }}
    QListWidget#SetupPickerList::item:selected {{
        background-color: {ACCENT}; color: white;
        border-left: 4px solid #ff7b87; font-weight: 700;
    }}
    QListWidget#CategoryList::item {{
        padding: 5px 10px; margin: 1px 0;
        border-left: 4px solid transparent;
    }}
    QListWidget#CategoryList::item:selected {{
        background-color: {ACCENT}; color: white;
        border-left: 4px solid #ff7b87; font-weight: 700;
    }}
    QListWidget#PartList {{
        background-color: transparent; border: none;
    }}
    QListWidget#PartList::item {{
        background-color: transparent; border: none; padding: 0;
    }}
    QListWidget#PartList::item:selected,
    QListWidget#PartList::item:hover {{ background-color: transparent; }}

    /* Google Drive-inspired folder picker */
    QDialog#DrivePicker {{ background-color: #202124; }}
    QLabel#DriveBreadcrumb {{
        background-color: #303134; color: #e8eaed;
        border: 1px solid #5f6368; border-radius: 18px;
        padding: 8px 14px; font-weight: 600;
    }}
    QPushButton#DriveAction {{
        background-color: #303134; color: #e8eaed;
        border: 1px solid #5f6368; border-radius: 18px;
        padding: 7px 14px; font-weight: 600;
    }}
    QPushButton#DriveAction:hover {{ background-color: #3c4043; }}
    QPushButton#DriveAction:disabled {{
        color: #777b85; border-color: #45474d;
    }}
    QListWidget#DriveFolderList {{
        background-color: #202124;
        border: 1px solid #5f6368; border-radius: 10px; padding: 6px;
    }}
    QListWidget#DriveFolderList::item {{
        background-color: transparent; color: #e8eaed;
        border-radius: 8px; padding: 8px 12px;
    }}
    QListWidget#DriveFolderList::item:hover:!selected {{
        background-color: #303134;
    }}
    QListWidget#DriveFolderList::item:selected {{
        background-color: #394457; color: white;
        border: 1px solid #8ab4f8;
    }}
    QTableWidget::item {{ padding: 5px 7px; }}
    QHeaderView::section {{
        background-color: {CARD}; color: {TEXT};
        border: none; border-right: 1px solid {BORDER};
        border-bottom: 1px solid {BORDER};
        padding: 7px 8px; font-weight: 600;
    }}
    QTableCornerButton::section {{
        background-color: {CARD}; border: 1px solid {BORDER};
    }}

    /* Scroll areas */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {BORDER}; border-radius: 5px; min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {MUTED}; }}

    /* Progress bar */
    QProgressBar {{ background: {SIDEBAR}; border: none; border-radius: 3px; height: 6px; }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

    /* Tabs */
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; top: -1px; }}
    QTabBar::tab {{
        background: {SIDEBAR}; color: {MUTED};
        padding: 8px 18px; margin-right: 2px;
        border-top-left-radius: 8px; border-top-right-radius: 8px;
        font-size: 13px; font-weight: 600;
    }}
    QTabBar::tab:selected {{ background: {ACCENT}; color: white; }}
    QTabBar::tab:hover:!selected {{ background: {CARD_HI}; color: {TEXT}; }}

    /* Restrained settings surfaces and clear action hierarchy. */
    QWidget#SettingsPage QFrame#Card {{
        background-color: #2d3035; border: 1px solid #41454c;
        border-radius: 10px;
    }}
    QWidget#SettingsPage QLabel#H1 {{ font-size: 24px; font-weight: 600; }}
    QWidget#SettingsPage QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
    QWidget#SettingsPage QLabel#Muted {{ color: #b0b6bf; }}
    QWidget#SettingsPage QPushButton {{ min-height: 22px; }}
    QWidget#SettingsPage QPushButton#Secondary,
    QWidget#SettingsPage QPushButton#Ghost {{
        background-color: #353940; color: #e7e9ed;
        border: 1px solid #505661; border-radius: 6px;
        padding: 7px 14px; font-weight: 500;
    }}
    QWidget#SettingsPage QPushButton#Secondary:hover,
    QWidget#SettingsPage QPushButton#Ghost:hover {{
        background-color: #41464f; border-color: #717985;
    }}
    QWidget#SettingsPage QPushButton:disabled {{
        color: #858b95; background-color: #303339; border-color: #41454c;
    }}
    QWidget#SettingsPage QLineEdit {{
        background-color: #24272b; border: 1px solid #505661;
        border-radius: 6px; padding: 9px 10px;
    }}
    QTabWidget#SettingsTabs::pane {{ border: none; }}
    QTabWidget#SettingsTabs QTabBar::tab {{
        background: transparent; color: #b0b6bf;
        border: none; border-bottom: 2px solid transparent;
        border-radius: 0; margin-right: 16px;
        padding: 12px 6px; font-size: 13px; font-weight: 600;
    }}
    QTabWidget#SettingsTabs QTabBar::tab:selected {{
        background: transparent; color: #ffffff;
        border-bottom: 2px solid {ACCENT};
    }}
    QTabWidget#SettingsTabs QTabBar::tab:hover:!selected {{
        color: #ffffff; background: #303339;
    }}
    """
