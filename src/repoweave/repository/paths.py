"""Path validation shared by repository inventory and capture."""

from pathlib import Path, PureWindowsPath


class RepositoryError(ValueError):
    """Repository capture failed without returning a partial snapshot."""


def validate_relative_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or PureWindowsPath(path).drive
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        raise RepositoryError(f"Unsafe relative path: {path!r}")
    try:
        path.encode("utf-8")
    except UnicodeError:
        raise RepositoryError("Repository paths must be valid UTF-8") from None
    return path


def excluded_relative_paths(root: Path, paths: tuple[Path, ...]) -> tuple[str, ...]:
    relative = set()
    for path in paths:
        # Resolve parents, not the final component: an excluded symlink is never followed.
        absolute = path.parent.resolve() / path.name
        if root == absolute or root.is_relative_to(absolute):
            raise RepositoryError("An output/data directory cannot contain the input root")
        if absolute.is_relative_to(root):
            relative.add(validate_relative_path(absolute.relative_to(root).as_posix()))
    return tuple(sorted(
        path for path in relative
        if not any(path.startswith(other + "/") for other in relative if other != path)
    ))
