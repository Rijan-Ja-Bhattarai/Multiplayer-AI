"""Shared model-purpose controls for the connection page and legacy dialog."""
from PySide6.QtWidgets import QCheckBox, QGridLayout, QPlainTextEdit, QVBoxLayout, QWidget

from network_a2a.orchestration import TASK_TYPES

from .widgets import label


class ModelPurpose(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("What should this model do?", "heading"))
        self.purpose = QPlainTextEdit()
        self.purpose.setMaximumHeight(80)
        self.purpose.setPlaceholderText("Describe its strengths and your preferences, e.g. Python coding and debugging; avoid creative writing.")
        layout.addWidget(self.purpose)
        layout.addWidget(label("Permitted tasks", "muted"))
        grid = QGridLayout()
        self.tasks = {}
        for index, (key, title) in enumerate(TASK_TYPES.items()):
            checkbox = QCheckBox(title)
            self.tasks[key] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        layout.addLayout(grid)
        self.enabled = QCheckBox("Allow Jev to assign work to this model")
        layout.addWidget(self.enabled)
        layout.addWidget(label("General chat works immediately with your connected model. Configure these optional preferences to let Jev delegate work across models. Purpose and permitted tasks are shared with your workspace and coordinator.", "muted", True))

    def values(self):
        return {"purpose": self.purpose.toPlainText().strip(),
                "tasks": [key for key, checkbox in self.tasks.items() if checkbox.isChecked()],
                "delegation_enabled": self.enabled.isChecked()}

    def load(self, profile):
        self.purpose.setPlainText(profile.get("purpose", ""))
        for key, checkbox in self.tasks.items():
            checkbox.setChecked(key in profile.get("tasks", []))
        self.enabled.setChecked(profile.get("delegation_enabled", False))
