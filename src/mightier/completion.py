"""Validated metadata for editor completion. Never include private notes."""

from mightier.books import BookError, read_entity


def collect(project):
    records = []
    errors = []
    for kind, path in project.entity_files():
        try:
            data = read_entity(kind, path)
        except (BookError, OSError, UnicodeError) as error:
            message = str(error)
            errors.append(
                message if message.startswith(f"{path}:") else f"{path}: {message}"
            )
            continue
        records.append(
            {
                "type": kind,
                "id": data["id"],
                "names": data["names"],
                "description": data.get("public_description", ""),
            }
        )
    return {
        "metadata_dir": str(project.metadata_dir),
        "records": records,
        "errors": errors,
    }
