"""Discord-inspired charcoal surfaces, blurple actions, and readable contrast."""
THEME = """
QWidget { color: #dbdee1; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #313338; }
QFrame#rail { background: #1e1f22; border: none; }
QFrame#sidebar { background: #2b2d31; border: none; }
QFrame#topbar { background: #313338; border-bottom: 1px solid #25272b; }
QFrame#profile { background: #232428; border: none; }
QFrame#card, QFrame#stat, QFrame#settings { background: #2b2d31; border: 1px solid #3d3f46; border-radius: 12px; }
QFrame#hero { background: #393865; border: 1px solid #535184; border-radius: 16px; }
QLabel { background: transparent; }
QLabel#title { font-size: 31px; font-weight: 700; color: #f2f3f5; }
QLabel#heroTitle { font-size: 34px; font-weight: 700; color: white; }
QLabel#heading { font-size: 19px; font-weight: 650; color: #f2f3f5; }
QLabel#muted { color: #a2a5af; }
QLabel#eyebrow { color: #b8b0fa; font-size: 10px; font-weight: 650; }
QLabel#statValue { font-size: 30px; color: white; font-weight: 650; }
QLabel#online { color: #3ba55d; }
QPushButton { background: #404249; border: 0; border-radius: 7px; padding: 10px 16px; color: #dbdee1; font-weight: 550; }
QPushButton:hover { background: #4e5058; color: white; }
QPushButton:pressed { background: #383a40; }
QPushButton:disabled { background: #36383f; color: #72757f; }
QPushButton#primary { background: #5865f2; color: white; }
QPushButton#primary:hover { background: #7983f5; }
QPushButton#primary:pressed { background: #4752c4; }
QPushButton#primary:disabled { background: #414778; color: #9297bd; }
QPushButton#nav { background: transparent; text-align: left; color: #a7a9b3; padding: 11px 15px; }
QPushButton#nav:hover { background: #35373c; color: #dbdee1; }
QPushButton#nav:checked { background: #404249; color: white; }
QPushButton#workspace { background: #313338; border-radius: 16px; font-size: 20px; padding: 0; }
QPushButton#workspace:hover, QPushButton#workspace:checked { background: #5865f2; border-radius: 16px; color: white; }
QPushButton#ghost { background: transparent; color: #b6b8c2; padding: 7px 10px; }
QPushButton#ghost:hover { background: #3c3e45; }
QPushButton#danger { background: #402c30; color: #f08b8e; }
QLineEdit, QPlainTextEdit, QComboBox { background: #1e1f22; color: #dbdee1; border: 1px solid #404249; border-radius: 7px; padding: 10px; selection-background-color: #5865f2; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #7983f5; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #232428; color: #dbdee1; selection-background-color: #5865f2; padding: 5px; }
QCheckBox { spacing: 8px; color: #b5bac1; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; background: #1e1f22; border: 1px solid #666a75; }
QCheckBox::indicator:checked { background: #5865f2; border-color: #7983f5; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #2b2d31; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #1a1b1e; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { padding: 14px 12px; border-radius: 7px; color: #b5bac1; }
QListWidget::item:selected { background: #404249; color: white; }
QListWidget::item:hover { background: #383a40; }
QTableWidget { background: #2b2d31; alternate-background-color: #313338; color: #dbdee1; border: 1px solid #404249; gridline-color: #404249; selection-background-color: #404249; selection-color: white; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #232428; color: #b5bac1; border: none; border-right: 1px solid #404249; border-bottom: 1px solid #404249; padding: 10px 8px; }
QTableCornerButton::section { background: #232428; border: none; }
QMenu { background: #232428; color: #dbdee1; border: 1px solid #404249; padding: 5px; }
QMenu::item { padding: 9px 16px; border-radius: 4px; }
QMenu::item:selected { background: #5865f2; color: white; }
QProgressBar { background: #26272c; border: none; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #5865f2; border-radius: 3px; }
QToolTip { background: #111214; color: #dbdee1; border: none; padding: 8px; }
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
