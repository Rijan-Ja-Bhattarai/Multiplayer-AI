"""Black surfaces and white text throughout the native desktop interface."""
THEME = """
QWidget { background: #000000; color: #ffffff; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #000000; }
QFrame#rail, QFrame#sidebar, QFrame#profile { background: #000000; border: none; }
QFrame#topbar { background: #000000; border-bottom: 1px solid #333333; }
QFrame#card, QFrame#stat, QFrame#settings { background: #000000; border: 1px solid #333333; border-radius: 12px; }
QLabel { background: transparent; }
QLabel#title { font-size: 31px; font-weight: 700; }
QLabel#heading { font-size: 19px; font-weight: 650; }
QLabel#eyebrow { font-size: 10px; font-weight: 650; }
QLabel#statValue, QPushButton#statValue { font-size: 30px; color: #ffffff; font-weight: 650; }
QPushButton { background: #000000; border: 1px solid #555555; border-radius: 7px; padding: 10px 16px; color: #ffffff; font-weight: 550; }
QPushButton:hover, QPushButton:pressed, QPushButton:checked { background: #000000; border-color: #ffffff; }
QPushButton:disabled { background: #000000; color: #ffffff; border-color: #222222; }
QPushButton#primary { background: #000000; color: #ffffff; border-color: #ffffff; }
QPushButton#nav { background: #000000; text-align: left; color: #ffffff; padding: 11px 15px; border-color: transparent; }
QPushButton#nav:checked, QPushButton#nav:hover { border-color: #ffffff; }
QPushButton#workspace { background: #000000; border-radius: 16px; font-size: 20px; padding: 0; }
QPushButton#workspace:hover, QPushButton#workspace:checked { background: #000000; border-color: #ffffff; color: #ffffff; }
QPushButton#ghost { background: #000000; color: #ffffff; padding: 7px 10px; border-color: transparent; }
QPushButton#ghost:hover { border-color: #ffffff; }
QPushButton#statValue { text-align: left; padding: 0; border: none; }
QPushButton#statValue:hover { text-decoration: underline; }
QPushButton#danger { background: #000000; color: #ffffff; }
QLineEdit, QPlainTextEdit, QComboBox { background: #000000; color: #ffffff; border: 1px solid #555555; border-radius: 7px; padding: 10px; selection-background-color: #ffffff; selection-color: #000000; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #ffffff; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #000000; color: #ffffff; selection-background-color: #000000; selection-color: #ffffff; padding: 5px; }
QCheckBox { spacing: 8px; color: #ffffff; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; background: #000000; border: 1px solid #ffffff; }
QCheckBox::indicator:checked { background: #ffffff; border-color: #ffffff; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #000000; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #ffffff; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { padding: 14px 12px; border-radius: 7px; color: #ffffff; border: 1px solid transparent; }
QListWidget::item:selected, QListWidget::item:hover { background: #000000; color: #ffffff; border-color: #ffffff; }
QTableWidget { background: #000000; alternate-background-color: #000000; color: #ffffff; border: 1px solid #555555; gridline-color: #333333; selection-background-color: #000000; selection-color: #ffffff; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #000000; color: #ffffff; border: none; border-right: 1px solid #555555; border-bottom: 1px solid #555555; padding: 10px 8px; }
QTableCornerButton::section { background: #000000; border: none; }
QMenu { background: #000000; color: #ffffff; border: 1px solid #555555; padding: 5px; }
QMenu::item { padding: 9px 16px; border-radius: 4px; }
QMenu::item:selected { background: #000000; color: #ffffff; border: 1px solid #ffffff; }
QProgressBar { background: #000000; border: none; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #ffffff; border-radius: 3px; }
QToolTip { background: #000000; color: #ffffff; border: 1px solid #ffffff; padding: 8px; }
QDialogButtonBox QPushButton { min-width: 85px; }
"""

PROVIDER_NAMES = {"ollama": ("Ollama", "Local models, on your terms.", "O", "#a59af5"),
    "bionic": ("Bionic GPT", "Your private Bionic deployment.", "B", "#58b89c"),
    "openai": ("OpenAI", "Responses API for your team.", "◎", "#67bd9a"),
    "anthropic": ("Claude", "A thoughtful collaboration partner.", "✳", "#d8a184"),
    "gemini": ("Gemini", "Google models in your workspace.", "✦", "#7eacff"),
    "groq": ("Groq", "Fast inference. More momentum.", "g", "#ee9b7d"),
    "deepseek": ("DeepSeek", "A fresh perspective on your work.", "≈", "#78a6eb"),
    "mistral": ("Mistral", "Your choice of Mistral models.", "M", "#e7b16e"),
    "openrouter": ("OpenRouter", "Many models. One connection.", "↗", "#a69aee"),
    "openai-compatible": ("Custom endpoint", "Connect LM Studio, vLLM, and more.", "◇", "#a69aee")}
