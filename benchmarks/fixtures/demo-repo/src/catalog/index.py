"""Unrelated catalog indexing code included as context noise."""


def normalize_product_name(name: str) -> str:
    return " ".join(name.casefold().split())


def build_catalog_index(names: list[str]) -> dict[str, int]:
    return {
        normalize_product_name(name): position for position, name in enumerate(names)
    }
