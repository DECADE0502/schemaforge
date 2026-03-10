"""SchemaForge 器件库存储

器件库存储层：JSON文件（源数据）+ SQLite（索引）。

- 每个器件对应一个 JSON 文件（store/devices/<part>.json）
- SQLite 数据库用于快速检索（按类别、关键字等）
- rebuild_index() 可从 JSON 文件重建索引
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

from schemaforge.library.models import DeviceModel


class ComponentStore:
    """器件库存储 -- JSON文件(源数据) + SQLite(索引)"""

    def __init__(self, store_dir: Path) -> None:
        self.store_dir = Path(store_dir)
        self.devices_dir = self.store_dir / "devices"
        self.datasheets_dir = self.store_dir / "datasheets"
        self.db_path = self.store_dir / "library.db"
        self.devices_dir.mkdir(parents=True, exist_ok=True)
        self.datasheets_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ----------------------------------------------------------
    # 数据库初始化
    # ----------------------------------------------------------

    def _init_db(self) -> None:
        """初始化 SQLite 索引表结构"""
        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    part_number  TEXT PRIMARY KEY,
                    manufacturer TEXT NOT NULL DEFAULT '',
                    category     TEXT NOT NULL DEFAULT '',
                    description  TEXT NOT NULL DEFAULT '',
                    package      TEXT NOT NULL DEFAULT '',
                    lcsc_part    TEXT NOT NULL DEFAULT '',
                    json_path    TEXT NOT NULL,
                    source       TEXT NOT NULL DEFAULT 'manual',
                    design_roles TEXT NOT NULL DEFAULT ''
                )
                """
            )
            existing_cols = {
                row[1] for row in con.execute("PRAGMA table_info(devices)").fetchall()
            }
            if "design_roles" not in existing_cols:
                con.execute(
                    "ALTER TABLE devices ADD COLUMN design_roles TEXT NOT NULL DEFAULT ''"
                )
            con.commit()
        finally:
            con.close()

    # ----------------------------------------------------------
    # CRUD
    # ----------------------------------------------------------

    def save_device(self, device: DeviceModel) -> Path:
        """保存器件到 JSON 文件并更新索引

        Returns:
            JSON 文件路径
        """
        filename = self._sanitize_filename(device.part_number) + ".json"
        filepath = self.devices_dir / filename
        json_str = device.model_dump_json(indent=2)
        filepath.write_text(json_str, encoding="utf-8")
        self._index_device(device)
        return filepath

    def get_device(self, part_number: str) -> DeviceModel | None:
        """根据料号加载器件

        Returns:
            DeviceModel 或 None（找不到时）
        """
        con = sqlite3.connect(str(self.db_path))
        try:
            row = con.execute(
                "SELECT json_path FROM devices WHERE part_number = ?",
                (part_number,),
            ).fetchone()
        finally:
            con.close()

        if row is None:
            return None

        filepath = Path(row[0])
        if not filepath.is_absolute():
            filepath = self.store_dir / filepath
        if not filepath.exists():
            return None

        raw = filepath.read_text(encoding="utf-8")
        return DeviceModel.model_validate_json(raw)

    def search_devices(
        self,
        category: str = "",
        query: str = "",
        **filters: str,
    ) -> list[DeviceModel]:
        """使用 SQLite 索引搜索器件

        Args:
            category: 按类别筛选（精确匹配）
            query: 关键字搜索（匹配 part_number / description / manufacturer）
            **filters: 其他字段精确匹配（如 package="SOT-223"）

        Returns:
            匹配的 DeviceModel 列表
        """
        clauses: list[str] = []
        params: list[str] = []

        if category:
            clauses.append("category = ?")
            params.append(category)

        if query:
            clauses.append(
                "(part_number LIKE ? OR description LIKE ? OR manufacturer LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like])

        for col, val in filters.items():
            if col in ("manufacturer", "package", "lcsc_part", "source"):
                clauses.append(f"{col} = ?")
                params.append(val)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT json_path FROM devices WHERE {where}"  # noqa: S608

        con = sqlite3.connect(str(self.db_path))
        try:
            rows = con.execute(sql, params).fetchall()
        finally:
            con.close()

        results: list[DeviceModel] = []
        for (json_path,) in rows:
            fp = Path(json_path)
            if not fp.is_absolute():
                fp = self.store_dir / fp
            if fp.exists():
                raw = fp.read_text(encoding="utf-8")
                results.append(DeviceModel.model_validate_json(raw))
        return results

    def list_devices(self) -> list[str]:
        """列出所有料号

        Returns:
            料号字符串列表
        """
        con = sqlite3.connect(str(self.db_path))
        try:
            rows = con.execute(
                "SELECT part_number FROM devices ORDER BY part_number"
            ).fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]

    def save_datasheet(self, part_number: str, source_path: str) -> str:
        """将 PDF datasheet 复制到 store/datasheets/ 目录。

        Args:
            part_number: 器件型号（用于命名目标文件）
            source_path: 源 PDF 文件路径

        Returns:
            相对于 store_dir 的路径（如 "datasheets/TPS54202.pdf"）
        """
        src = Path(source_path)
        if not src.exists():
            return ""

        suffix = src.suffix.lower() or ".pdf"
        filename = self._sanitize_filename(part_number) + suffix
        dst = self.datasheets_dir / filename
        shutil.copy2(str(src), str(dst))
        return str(Path("datasheets") / filename)

    def get_datasheet_abspath(self, part_number: str) -> Path | None:
        """获取器件 datasheet 的绝对路径（若存在）。

        先从 DeviceModel.datasheet_path 读取，若为空则尝试推断文件名。
        """
        device = self.get_device(part_number)
        if device is None:
            return None

        if device.datasheet_path:
            fp = self.store_dir / device.datasheet_path
            if fp.exists():
                return fp

        # 回退: 按约定文件名查找
        for suffix in (".pdf", ".PDF"):
            candidate = self.datasheets_dir / (
                self._sanitize_filename(part_number) + suffix
            )
            if candidate.exists():
                return candidate
        return None

    def delete_device(self, part_number: str) -> bool:
        """从库中删除器件（JSON文件 + 索引 + datasheet）

        Returns:
            True 表示删除成功，False 表示不存在
        """
        con = sqlite3.connect(str(self.db_path))
        try:
            row = con.execute(
                "SELECT json_path FROM devices WHERE part_number = ?",
                (part_number,),
            ).fetchone()
            if row is None:
                return False
            con.execute(
                "DELETE FROM devices WHERE part_number = ?",
                (part_number,),
            )
            con.commit()
        finally:
            con.close()

        # 删除 JSON 文件
        filepath = Path(row[0])
        if not filepath.is_absolute():
            filepath = self.store_dir / filepath
        if filepath.exists():
            filepath.unlink()

        # 删除关联的 datasheet 文件
        for suffix in (".pdf", ".PDF"):
            ds_path = self.datasheets_dir / (
                self._sanitize_filename(part_number) + suffix
            )
            if ds_path.exists():
                ds_path.unlink()

        return True

    def rebuild_index(self) -> None:
        """从所有 JSON 文件重建 SQLite 索引"""
        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute("DELETE FROM devices")
            con.commit()
        finally:
            con.close()

        for fp in self.devices_dir.glob("*.json"):
            try:
                raw = fp.read_text(encoding="utf-8")
                device = DeviceModel.model_validate_json(raw)
                self._index_device(device)
            except Exception:
                # 跳过无法解析的文件
                continue

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def search_by_role(self, role: str) -> list[DeviceModel]:
        """按设计角色搜索器件"""
        con = sqlite3.connect(str(self.db_path))
        try:
            rows = con.execute(
                "SELECT json_path FROM devices WHERE design_roles LIKE ?",
                (f"%{role}%",),
            ).fetchall()
        finally:
            con.close()

        results: list[DeviceModel] = []
        for (json_path,) in rows:
            fp = Path(json_path)
            if not fp.is_absolute():
                fp = self.store_dir / fp
            if fp.exists():
                raw = fp.read_text(encoding="utf-8")
                device = DeviceModel.model_validate_json(raw)
                if role in device.design_roles:
                    results.append(device)
        return results

    def _index_device(self, device: DeviceModel) -> None:
        """在 SQLite 索引中插入或更新器件记录"""
        filename = self._sanitize_filename(device.part_number) + ".json"
        json_path = str(Path("devices") / filename)
        design_roles = ",".join(device.design_roles) if device.design_roles else ""

        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute(
                """
                INSERT OR REPLACE INTO devices
                    (part_number, manufacturer, category, description,
                     package, lcsc_part, json_path, source, design_roles)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device.part_number,
                    device.manufacturer,
                    device.category,
                    device.description,
                    device.package,
                    device.lcsc_part,
                    json_path,
                    device.source,
                    design_roles,
                ),
            )
            con.commit()
        finally:
            con.close()

    @staticmethod
    def _sanitize_filename(part_number: str) -> str:
        """将料号转换为安全的文件名

        替换 / \\ : * ? " < > | 和空格为下划线
        """
        return re.sub(r'[/\\:*?"<>|\s]+', "_", part_number)
