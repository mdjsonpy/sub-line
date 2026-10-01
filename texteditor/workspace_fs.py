"""In-memory workspace filesystem: no decrypted data ever touches disk.

:class:`MemoryTree` stores a file tree as ``{relative_path: bytes}`` in RAM.
:class:`WorkspaceFS` is the unified interface the app uses for all I/O —
it either delegates to the real filesystem (plain workspaces) or to a
:class:`MemoryTree` (encrypted workspaces).
"""
from __future__ import annotations

import io
import os
import tarfile


class MemoryTree:
    """In-memory file tree.

    Keys are relative paths with ``/`` separators (POSIX style), e.g.
    ``"src/main.py"``, ``"docs/readme.md"``.  Directory entries are
    implicit: a path ``"src"`` is a directory if any key starts with
    ``"src/"``.
    """

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    # --- queries ---------------------------------------------------------------
    def listdir(self, rel: str = "") -> list[str]:
        """Return immediate children names (files and dirs) of ``rel``."""
        prefix = rel.rstrip("/") + "/" if rel else ""
        seen: set[str] = set()
        out: list[str] = []
        for key in sorted(self._files):
            if not key.startswith(prefix):
                continue
            remainder = key[len(prefix):]
            top = remainder.split("/", 1)[0]
            if top not in seen:
                seen.add(top)
                out.append(top)
        return out

    def isdir(self, rel: str) -> bool:
        rel = rel.rstrip("/")
        prefix = rel + "/" if rel else ""
        if not rel:
            return True
        return any(key.startswith(prefix) for key in self._files)

    def isfile(self, rel: str) -> bool:
        return rel.rstrip("/") in self._files

    def exists(self, rel: str) -> bool:
        return self.isdir(rel) or self.isfile(rel)

    def size(self, rel: str) -> int:
        data = self._files.get(rel.rstrip("/"))
        return len(data) if data is not None else 0

    # --- I/O -------------------------------------------------------------------
    def read_bytes(self, rel: str) -> bytes:
        key = rel.rstrip("/")
        if key not in self._files:
            raise FileNotFoundError(f"no such file in tree: {rel}")
        return self._files[key]

    def write_bytes(self, rel: str, data: bytes) -> None:
        key = rel.rstrip("/")
        if not key:
            raise ValueError("cannot write to root")
        # ensure parent dirs exist implicitly (they do — dirs are derived)
        self._files[key] = data

    # --- mutations -------------------------------------------------------------
    def makedirs(self, rel: str) -> None:
        # no-op: directories are implicit; nothing to do
        pass

    def remove(self, rel: str) -> None:
        key = rel.rstrip("/")
        if key not in self._files:
            raise FileNotFoundError(f"no such file in tree: {rel}")
        del self._files[key]

    def rmtree(self, rel: str) -> None:
        rel = rel.rstrip("/")
        if not rel:
            self._files.clear()
            return
        prefix = rel + "/"
        to_del = [k for k in self._files if k.startswith(prefix) or k == rel]
        for k in to_del:
            del self._files[k]
        if not to_del:
            raise FileNotFoundError(f"no such path in tree: {rel}")

    def rename(self, src: str, dst: str) -> None:
        src = src.rstrip("/")
        dst = dst.rstrip("/")
        src_prefix = src + "/"
        moved: dict[str, bytes] = {}
        if src in self._files:
            moved[dst] = self._files.pop(src)
        for key in [k for k in self._files if k.startswith(src_prefix)]:
            new_key = dst + "/" + key[len(src_prefix):]
            moved[new_key] = self._files.pop(key)
        if not moved:
            raise FileNotFoundError(f"no such path in tree: {src}")
        self._files.update(moved)

    # --- serialization ---------------------------------------------------------
    def pack_to_tar(self) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for rel in sorted(self._files):
                info = tarfile.TarInfo(name=rel)
                info.size = len(self._files[rel])
                tf.addfile(info, io.BytesIO(self._files[rel]))
        return buf.getvalue()

    @classmethod
    def unpack_from_tar(cls, tar: bytes) -> MemoryTree:
        tree = cls()
        with tarfile.open(fileobj=io.BytesIO(tar), mode="r") as tf:
            for member in tf.getmembers():
                if member.isfile():
                    f = tf.extractfile(member)
                    if f is not None:
                        # Normalize: strip leading "./" and any absolute prefix
                        name = member.name
                        if name.startswith("./"):
                            name = name[2:]
                        elif name.startswith("/"):
                            # strip absolute prefix (keep only relative part)
                            name = name.lstrip("/")
                        if name:
                            tree._files[name] = f.read()
        return tree

    # --- introspection ---------------------------------------------------------
    @property
    def files(self) -> dict[str, bytes]:
        return dict(self._files)

    def __len__(self) -> int:
        return len(self._files)

    def __contains__(self, rel: str) -> bool:
        return rel.rstrip("/") in self._files


class WorkspaceFS:
    """Unified filesystem interface for a workspace.

    If ``tree`` is provided, all operations are served from memory.
    Otherwise, operations delegate to the real filesystem rooted at ``root``.
    """

    def __init__(self, root: str, tree: MemoryTree | None = None) -> None:
        self._root = os.path.abspath(root)
        self._tree = tree

    @classmethod
    def from_tree(cls, tree: MemoryTree, root: str = "") -> WorkspaceFS:
        return cls(root=root or "/vault", tree=tree)

    @property
    def root(self) -> str:
        return self._root

    @property
    def is_memory(self) -> bool:
        return self._tree is not None

    @property
    def tree(self) -> MemoryTree | None:
        return self._tree

    # --- path helpers ----------------------------------------------------------
    def abspath(self, path: str) -> str:
        return os.path.abspath(path)

    def join(self, *parts: str) -> str:
        return os.path.join(*parts)

    def dirname(self, path: str) -> str:
        return os.path.dirname(path)

    def basename(self, path: str) -> str:
        return os.path.basename(path)

    def relpath(self, path: str) -> str:
        """Absolute path → relative to workspace root (POSIX-style).

        If ``path`` is already relative, it is returned as-is (normalized).
        """
        if os.path.isabs(path):
            rel = os.path.relpath(path, self._root)
        else:
            rel = path
        return rel.replace(os.sep, "/") if rel not in (".", "") else ""

    # --- queries ---------------------------------------------------------------
    def listdir(self, path: str) -> list[str]:
        if self._tree is not None:
            return self._tree.listdir(self.relpath(path))
        return os.listdir(path)

    def isdir(self, path: str) -> bool:
        if self._tree is not None:
            return self._tree.isdir(self.relpath(path))
        return os.path.isdir(path)

    def isfile(self, path: str) -> bool:
        if self._tree is not None:
            return self._tree.isfile(self.relpath(path))
        return os.path.isfile(path)

    def exists(self, path: str) -> bool:
        if self._tree is not None:
            return self._tree.exists(self.relpath(path))
        return os.path.exists(path)

    def size(self, path: str) -> int:
        if self._tree is not None:
            return self._tree.size(self.relpath(path))
        return os.stat(path).st_size

    # --- I/O -------------------------------------------------------------------
    def read_bytes(self, path: str) -> bytes:
        if self._tree is not None:
            return self._tree.read_bytes(self.relpath(path))
        with open(path, "rb") as fh:
            return fh.read()

    def read_text(self, path: str) -> str:
        return self.read_bytes(path).decode("utf-8")

    def write_bytes(self, path: str, data: bytes) -> None:
        if self._tree is not None:
            self._tree.write_bytes(self.relpath(path), data)
            return
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    # --- mutations -------------------------------------------------------------
    def makedirs(self, path: str) -> None:
        if self._tree is not None:
            self._tree.makedirs(self.relpath(path))
            return
        os.makedirs(path, exist_ok=True)

    def remove(self, path: str) -> None:
        if self._tree is not None:
            self._tree.remove(self.relpath(path))
            return
        os.remove(path)

    def rmtree(self, path: str) -> None:
        if self._tree is not None:
            self._tree.rmtree(self.relpath(path))
            return
        import shutil
        shutil.rmtree(path)

    def rename(self, src: str, dst: str) -> None:
        if self._tree is not None:
            self._tree.rename(self.relpath(src), self.relpath(dst))
            return
        import shutil
        shutil.move(src, dst)
