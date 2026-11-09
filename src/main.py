"""Project initialization metadata."""

PROJECT_NAME = "app-linux-vuln-sentinel"
PROJECT_STATUS = "design-review"


def get_project_metadata() -> dict[str, str]:
    """Return stable metadata used by initialization checks."""
    return {"name": PROJECT_NAME, "status": PROJECT_STATUS}

