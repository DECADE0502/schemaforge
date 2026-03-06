"""器件手动录入表单

支持手动填写器件基础信息 + 引脚定义，生成 DeviceDraft。
所有 UI 文案为中文。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from schemaforge.library.validator import DeviceDraft, PinDraft, VALID_CATEGORIES


class DeviceForm(QWidget):
    """器件手动录入表单

    信号:
        draft_ready(DeviceDraft): 用户完成填写并提交
    """

    draft_ready = Signal(object)  # DeviceDraft

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题
        title = QLabel("手动录入器件")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        layout.addWidget(title)

        # 滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        form_layout = QVBoxLayout(content)

        # ── 基础信息组 ──
        basic_group = QGroupBox("基础信息")
        basic_form = QFormLayout(basic_group)
        basic_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.part_number_edit = QLineEdit()
        self.part_number_edit.setPlaceholderText("如: TPS54202、AMS1117-3.3")
        basic_form.addRow("料号 *:", self.part_number_edit)

        self.manufacturer_edit = QLineEdit()
        self.manufacturer_edit.setPlaceholderText("如: Texas Instruments、AMS")
        basic_form.addRow("制造商:", self.manufacturer_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItem("")
        sorted_cats = sorted(VALID_CATEGORIES)
        for cat in sorted_cats:
            self.category_combo.addItem(cat)
        basic_form.addRow("类别:", self.category_combo)

        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("如: 3.3V 1A 低压差线性稳压器")
        basic_form.addRow("描述:", self.description_edit)

        self.package_edit = QLineEdit()
        self.package_edit.setPlaceholderText("如: SOT-223、SOT-23-6")
        basic_form.addRow("封装:", self.package_edit)

        self.lcsc_edit = QLineEdit()
        self.lcsc_edit.setPlaceholderText("如: C87774")
        basic_form.addRow("LCSC编号:", self.lcsc_edit)

        self.datasheet_edit = QLineEdit()
        self.datasheet_edit.setPlaceholderText("https://...")
        basic_form.addRow("Datasheet:", self.datasheet_edit)

        form_layout.addWidget(basic_group)

        # ── 引脚定义组 ──
        pin_group = QGroupBox("引脚定义")
        pin_layout = QVBoxLayout(pin_group)

        # 引脚数控制
        pin_ctrl = QHBoxLayout()
        pin_ctrl.addWidget(QLabel("引脚数:"))
        self.pin_count_spin = QSpinBox()
        self.pin_count_spin.setRange(0, 200)
        self.pin_count_spin.setValue(0)
        self.pin_count_spin.valueChanged.connect(self._on_pin_count_changed)
        pin_ctrl.addWidget(self.pin_count_spin)

        add_pin_btn = QPushButton("+ 添加引脚")
        add_pin_btn.clicked.connect(self._add_pin_row)
        pin_ctrl.addWidget(add_pin_btn)

        remove_pin_btn = QPushButton("- 删除末行")
        remove_pin_btn.clicked.connect(self._remove_last_pin)
        pin_ctrl.addWidget(remove_pin_btn)

        pin_ctrl.addStretch()
        pin_layout.addLayout(pin_ctrl)

        # 引脚表格
        self.pin_table = QTableWidget(0, 5)
        self.pin_table.setHorizontalHeaderLabels([
            "名称", "编号", "类型", "方位", "说明",
        ])
        header = self.pin_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.pin_table.setColumnWidth(1, 60)
        self.pin_table.setColumnWidth(2, 100)
        self.pin_table.setColumnWidth(3, 80)
        self.pin_table.setMinimumHeight(200)
        pin_layout.addWidget(self.pin_table)

        form_layout.addWidget(pin_group)

        # ── 电气参数组 ──
        specs_group = QGroupBox("电气参数 (可选)")
        specs_layout = QVBoxLayout(specs_group)

        self.specs_text = QPlainTextEdit()
        self.specs_text.setPlaceholderText(
            "每行一个参数，格式: key=value\n"
            "示例:\n"
            "v_in_max=28V\n"
            "v_out_typ=3.3V\n"
            "i_out_max=2A"
        )
        self.specs_text.setMaximumHeight(100)
        specs_layout.addWidget(self.specs_text)

        form_layout.addWidget(specs_group)

        # ── 备注 ──
        notes_group = QGroupBox("备注")
        notes_layout = QVBoxLayout(notes_group)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(60)
        notes_layout.addWidget(self.notes_edit)
        form_layout.addWidget(notes_group)

        form_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # ── 按钮区 ──
        btn_layout = QHBoxLayout()

        self.submit_btn = QPushButton("提交入库")
        self.submit_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        self.submit_btn.setMinimumHeight(40)
        self.submit_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; "
            "border-radius: 6px; padding: 8px 20px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self.submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(self.submit_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setMinimumHeight(40)
        clear_btn.clicked.connect(self.clear)
        btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)

    # ── 引脚表格操作 ──

    def _add_pin_row(self) -> None:
        """添加一行引脚"""
        row = self.pin_table.rowCount()
        self.pin_table.insertRow(row)

        # 名称
        self.pin_table.setItem(row, 0, QTableWidgetItem(""))
        # 编号
        self.pin_table.setItem(row, 1, QTableWidgetItem(str(row + 1)))

        # 类型下拉
        type_combo = QComboBox()
        type_combo.addItems(["", "input", "output", "power", "passive", "nc", "bidirectional"])
        self.pin_table.setCellWidget(row, 2, type_combo)

        # 方位下拉
        side_combo = QComboBox()
        side_combo.addItems(["left", "right", "top", "bottom"])
        self.pin_table.setCellWidget(row, 3, side_combo)

        # 说明
        self.pin_table.setItem(row, 4, QTableWidgetItem(""))

        # 同步 spinbox
        self.pin_count_spin.blockSignals(True)
        self.pin_count_spin.setValue(self.pin_table.rowCount())
        self.pin_count_spin.blockSignals(False)

    def _remove_last_pin(self) -> None:
        """删除最后一行引脚"""
        row_count = self.pin_table.rowCount()
        if row_count > 0:
            self.pin_table.removeRow(row_count - 1)
            self.pin_count_spin.blockSignals(True)
            self.pin_count_spin.setValue(self.pin_table.rowCount())
            self.pin_count_spin.blockSignals(False)

    def _on_pin_count_changed(self, count: int) -> None:
        """引脚数 spinbox 变化 → 调整表格行数"""
        current = self.pin_table.rowCount()
        if count > current:
            for _ in range(count - current):
                self._add_pin_row()
        elif count < current:
            for _ in range(current - count):
                self.pin_table.removeRow(self.pin_table.rowCount() - 1)

    # ── 数据收集 ──

    def collect_draft(self) -> DeviceDraft:
        """从表单收集数据生成 DeviceDraft"""
        # 引脚
        pins: list[PinDraft] = []
        for row in range(self.pin_table.rowCount()):
            name_item = self.pin_table.item(row, 0)
            num_item = self.pin_table.item(row, 1)
            type_widget = self.pin_table.cellWidget(row, 2)
            side_widget = self.pin_table.cellWidget(row, 3)
            desc_item = self.pin_table.item(row, 4)

            pins.append(PinDraft(
                name=name_item.text().strip() if name_item else "",
                number=num_item.text().strip() if num_item else str(row + 1),
                pin_type=type_widget.currentText() if isinstance(type_widget, QComboBox) else "",
                side=side_widget.currentText() if isinstance(side_widget, QComboBox) else "left",
                description=desc_item.text().strip() if desc_item else "",
            ))

        # 电气参数
        specs: dict[str, str] = {}
        for line in self.specs_text.toPlainText().strip().splitlines():
            line = line.strip()
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key:
                    specs[key] = val

        return DeviceDraft(
            part_number=self.part_number_edit.text().strip(),
            manufacturer=self.manufacturer_edit.text().strip(),
            category=self.category_combo.currentText().strip(),
            description=self.description_edit.text().strip(),
            package=self.package_edit.text().strip(),
            lcsc_part=self.lcsc_edit.text().strip(),
            datasheet_url=self.datasheet_edit.text().strip(),
            pins=pins,
            pin_count=len(pins),
            specs=specs,
            source="manual",
            notes=self.notes_edit.toPlainText().strip(),
        )

    def _on_submit(self) -> None:
        """提交按钮"""
        draft = self.collect_draft()
        if not draft.part_number:
            QMessageBox.warning(self, "提示", "料号不能为空！")
            return
        self.draft_ready.emit(draft)

    def clear(self) -> None:
        """清空表单"""
        self.part_number_edit.clear()
        self.manufacturer_edit.clear()
        self.category_combo.setCurrentIndex(0)
        self.description_edit.clear()
        self.package_edit.clear()
        self.lcsc_edit.clear()
        self.datasheet_edit.clear()
        self.pin_table.setRowCount(0)
        self.pin_count_spin.setValue(0)
        self.specs_text.clear()
        self.notes_edit.clear()

    def load_draft(self, draft: DeviceDraft) -> None:
        """从 DeviceDraft 加载数据到表单（用于编辑/EasyEDA导入后补全）"""
        self.part_number_edit.setText(draft.part_number)
        self.manufacturer_edit.setText(draft.manufacturer)

        # 设置类别
        idx = self.category_combo.findText(draft.category)
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        else:
            self.category_combo.setEditText(draft.category)

        self.description_edit.setText(draft.description)
        self.package_edit.setText(draft.package)
        self.lcsc_edit.setText(draft.lcsc_part)
        self.datasheet_edit.setText(draft.datasheet_url)
        self.notes_edit.setPlainText(draft.notes)

        # 引脚
        self.pin_table.setRowCount(0)
        for pin in draft.pins:
            self._add_pin_row()
            row = self.pin_table.rowCount() - 1
            if self.pin_table.item(row, 0):
                self.pin_table.item(row, 0).setText(pin.name)
            if self.pin_table.item(row, 1):
                self.pin_table.item(row, 1).setText(pin.number)
            type_widget = self.pin_table.cellWidget(row, 2)
            if isinstance(type_widget, QComboBox):
                idx = type_widget.findText(pin.pin_type)
                if idx >= 0:
                    type_widget.setCurrentIndex(idx)
            side_widget = self.pin_table.cellWidget(row, 3)
            if isinstance(side_widget, QComboBox):
                idx = side_widget.findText(pin.side)
                if idx >= 0:
                    side_widget.setCurrentIndex(idx)
            if self.pin_table.item(row, 4):
                self.pin_table.item(row, 4).setText(pin.description)

        # 电气参数
        spec_lines = [f"{k}={v}" for k, v in draft.specs.items()]
        self.specs_text.setPlainText("\n".join(spec_lines))
