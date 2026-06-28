from src.catalog.items import list_items


def test_list_items() -> None:
    assert list_items() == ["starter"]
