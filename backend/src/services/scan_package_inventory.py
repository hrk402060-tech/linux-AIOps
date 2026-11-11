from collections.abc import Iterator


class PackageInventory(dict[str, str]):
    def __init__(self) -> None:
        super().__init__()
        self.sources: dict[str, tuple[str, str]] = {}


def parse_inventory(output: str, manager: str) -> PackageInventory:
    inventory = PackageInventory()
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            continue
        if manager == "dpkg" and len(fields) >= 5 and fields[4] != "installed":
            continue
        name = fields[0].split(":", 1)[0]
        inventory[name] = fields[1]
        if manager == "dpkg" and len(fields) >= 4 and fields[2] and fields[3]:
            inventory.sources[name] = (fields[2].split(":", 1)[0], fields[3])
    return inventory


def advisory_packages(
    affected: list[dict[str, object]], inventory: dict[str, str],
) -> Iterator[tuple[dict[str, object], str | None, str | None]]:
    for package in sorted(affected, key=lambda item: str(item.get("name", ""))):
        name = str(package["name"])
        if package.get("package_scope", "binary") != "source":
            installed = inventory.get(name)
            yield package, installed, installed
            continue
        # A source advisory cannot establish binary identity from a name guess.
        if not isinstance(inventory, PackageInventory):
            continue
        for binary, (source, version) in sorted(inventory.sources.items()):
            if source == name:
                yield {
                    **package, "name": binary, "source_package": source,
                    "source_installed_version": version,
                }, inventory[binary], version
