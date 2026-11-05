import sqlite3
from datetime import datetime
from threading import RLock
from typing import Optional

from deepagents.backends.protocol import (
    BackendProtocol, WriteResult, EditResult, FileInfo,
    LsResult, ReadResult, GrepResult, GlobResult,
)
from deepagents.backends.utils import (
    InvalidGlobPatternError, _glob_search_files,
    grep_matches_from_files, perform_string_replacement, slice_read_response,
)
from langchain.agents.middleware import AgentMiddleware


class SQLiteBackend(BackendProtocol):
    """将文件存储在 SQLite 数据库中的持久化后端。

    内容存为文本，支持按行号分页读取，适配 DeepAgents 0.7 的返回类型。
    默认使用内存数据库；传入数据库文件路径后可跨进程保留文件。
    同一个后端实例的文件由所有会话共享。
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        # LangGraph 会在线程池中调用文件工具。允许跨线程访问后，
        # 仍须用锁保护同一连接及完整的读取→修改→提交过程。
        self._lock = RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                size INTEGER NOT NULL,
                modified_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    # ---------- 内部工具 ----------
    def _get_file(self, path: str) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM files WHERE path = ?", (path,)
            )
            return cur.fetchone()

    def _all_files(self, prefix: str = "/") -> list[sqlite3.Row]:
        base = prefix.rstrip("/")
        directory = base + "/"
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM files WHERE path = ? OR substr(path, 1, ?) = ? ORDER BY path",
                (base, len(directory), directory),
            )
            return cur.fetchall()

    def _file_data(self) -> dict:
        return {
            row["path"]: {
                "content": row["content"], "encoding": "utf-8",
                "modified_at": row["modified_at"],
            }
            for row in self._all_files()
        }

    # ---------- 文件系统操作 ----------
    def ls(self, path: str) -> LsResult:
        path = path.rstrip("/") + "/"
        result = []
        seen_dirs = set()
        for row in self._all_files(path):
            fp = row["path"]
            remaining = fp[len(path):]
            if "/" in remaining:
                dir_name = path + remaining.split("/")[0] + "/"
                if dir_name not in seen_dirs:
                    seen_dirs.add(dir_name)
                    result.append(FileInfo(path=dir_name, is_dir=True))
            else:
                result.append(FileInfo(
                    path=fp, is_dir=False,
                    size=row["size"],
                    modified_at=row["modified_at"],
                ))
        return LsResult(entries=sorted(result, key=lambda item: item["path"]))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        row = self._get_file(file_path)
        if row is None:
            return ReadResult(error=f"File '{file_path}' not found")
        # 返回未添加行号的原文；行号与显示格式由新版中间件处理。
        return slice_read_response(
            {"content": row["content"], "encoding": "utf-8",
             "modified_at": row["modified_at"]},
            offset, limit,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        with self._lock, self._conn:
            if self._get_file(file_path) is not None:
                return WriteResult(
                    error=f"File '{file_path}' already exists (create-only)."
                )
            now = datetime.now().isoformat()
            self._conn.execute(
                "INSERT INTO files (path, content, size, modified_at) VALUES (?, ?, ?, ?)",
                (file_path, content, len(content.encode("utf-8")), now),
            )
        return WriteResult(path=file_path)

    def edit(
        self, file_path: str, old_string: str,
        new_string: str, replace_all: bool = False,
    ) -> EditResult:
        with self._lock, self._conn:
            row = self._get_file(file_path)
            if row is None:
                return EditResult(error=f"File '{file_path}' not found")
            result = perform_string_replacement(row["content"], old_string, new_string, replace_all)
            if isinstance(result, str):
                return EditResult(error=result)
            new_content, occurrences = result
            now = datetime.now().isoformat()
            self._conn.execute(
                "UPDATE files SET content = ?, size = ?, modified_at = ? WHERE path = ?",
                (new_content, len(new_content.encode("utf-8")), now, file_path),
            )
        return EditResult(path=file_path, occurrences=occurrences)

    def grep(
        self, pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        *, max_count: Optional[int] = None,
    ) -> GrepResult:
        return grep_matches_from_files(
            self._file_data(), pattern, path or "/", glob, max_count=max_count,
        )

    def glob(self, pattern: str, path: Optional[str] = None) -> GlobResult:
        files = self._file_data()
        try:
            matched = _glob_search_files(files, pattern, path)
        except InvalidGlobPatternError as error:
            return GlobResult(error=str(error))
        paths = [] if matched == "No files found" else matched.splitlines()
        return GlobResult(matches=[
            FileInfo(path=name, is_dir=False, size=len(files[name]["content"].encode("utf-8")),
                     modified_at=files[name]["modified_at"])
            for name in paths
        ])

    def close(self):
        with self._lock:
            self._conn.close()

AgentMiddleware