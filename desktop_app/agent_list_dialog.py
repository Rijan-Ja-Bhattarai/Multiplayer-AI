"""Inspect the model agents in a workspace or the selected conversation."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from .widgets import action, label


class AgentListDialog(QDialog):
    def __init__(self, window, chat_only=False):
        super().__init__(window)
        self.window = window
        self.chat_only = chat_only
        self.setWindowTitle("Agents in this chat" if chat_only else "Workspace agents")
        self.setMinimumSize(680, 350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        self.heading = label("", "heading")
        layout.addWidget(self.heading)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Agent", "Provider", "Model", "Purpose / permitted tasks", "Status", ""))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        layout.addWidget(action("Done", self.accept))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.finished.connect(self.timer.stop)
        self.timer.start()
        self.signature = None
        self.refresh()

    def refresh(self):
        import json
        agents = self.window.model_agents(self.chat_only)
        signature = json.dumps(agents, sort_keys=True)
        if signature == self.signature:
            return
        self.signature = signature
        self.heading.setText(f"{len(agents)} model agents " + ("in this chat" if self.chat_only else "in this workspace"))
        self.table.setRowCount(0)
        self.table.setRowCount(len(agents))
        for row, agent in enumerate(agents):
            for column, value in enumerate((agent["id"], agent.get("provider") or "—", agent.get("model") or "Configured model",
                                            self.window.model_purpose_summary(agent),
                                            "Online" if agent["online"] else "Offline")):
                self.table.setItem(row, column, QTableWidgetItem(value))
                self.table.item(row, column).setToolTip(value)
            if agent.get("profile"):
                self.table.setCellWidget(row, 5, action("Edit model", lambda checked=False, current=agent: self.edit(current)))
            self.table.setRowHeight(row, 46)

    def edit(self, agent):
        self.timer.stop()
        try:
            self.window.edit_agent(agent)
        finally:
            if self.isVisible():
                self.timer.start()
                self.refresh()
