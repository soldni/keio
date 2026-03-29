from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from kiko.client_protocol import KeepClientError
from kiko.gkeepapi_client import GkeepApiClient
from kiko.markdown_model import ChecklistItem

# ---------------------------------------------------------------------------
# Fakes that mimic gkeepapi's interface without importing it
# ---------------------------------------------------------------------------


@dataclass
class FakeTimestamps:
    updated: object = None


@dataclass
class FakeListItem:
    text: str = ""
    checked: bool = False
    _subitems: list[FakeListItem] = field(default_factory=list)

    @property
    def subitems(self) -> list[FakeListItem]:
        return self._subitems

    def indent(self, parent: FakeListItem) -> None:
        parent._subitems.append(self)


@dataclass
class FakeNote:
    id: str = ""
    title: str = ""
    text: str = ""
    trashed: bool = False
    blobs: list = field(default_factory=list)
    timestamps: FakeTimestamps = field(default_factory=FakeTimestamps)
    _class_name: str = "Note"

    def trash(self) -> None:
        self.trashed = True


@dataclass
class FakeList:
    id: str = ""
    title: str = ""
    trashed: bool = False
    blobs: list = field(default_factory=list)
    timestamps: FakeTimestamps = field(default_factory=FakeTimestamps)
    items: list[FakeListItem] = field(default_factory=list)
    _class_name: str = "List"
    _added: list = field(default_factory=list)

    def trash(self) -> None:
        self.trashed = True

    def add(self, text: str, checked: bool = False) -> FakeListItem:
        item = FakeListItem(text=text, checked=checked)
        self.items.append(item)
        self._added.append(item)
        return item


class FakeKeep:
    def __init__(self) -> None:
        self._notes: dict[str, FakeNote | FakeList] = {}
        self._counter = 0
        self.synced = False

    def all(self) -> list[FakeNote | FakeList]:
        return list(self._notes.values())

    def get(self, note_id: str) -> FakeNote | FakeList | None:
        return self._notes.get(note_id)

    def createNote(self, title: str | None = None, text: str | None = None) -> FakeNote:
        self._counter += 1
        note = FakeNote(id=f"n{self._counter:04d}", title=title or "", text=text or "")
        self._notes[note.id] = note
        return note

    def createList(
        self, title: str | None = None, items: list[tuple[str, bool]] | None = None
    ) -> FakeList:
        self._counter += 1
        list_items = [FakeListItem(text=t, checked=c) for t, c in (items or [])]
        flist = FakeList(id=f"n{self._counter:04d}", title=title or "", items=list_items)
        self._notes[flist.id] = flist
        return flist

    def sync(self) -> None:
        self.synced = True


# Patch gkeepapi.node so isinstance checks work in GkeepApiClient._convert_note
@pytest.fixture(autouse=True)
def _patch_gkeepapi_node(monkeypatch) -> None:
    """Make gkeepapi.node.List point to our FakeList so isinstance() works."""
    import types

    fake_node_module = types.ModuleType("gkeepapi.node")
    fake_node_module.List = FakeList  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "gkeepapi.node", fake_node_module)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_notes_filters_trashed() -> None:
    keep = FakeKeep()
    keep._notes["a"] = FakeNote(id="a", title="Active")
    keep._notes["b"] = FakeNote(id="b", title="Trashed", trashed=True)

    client = GkeepApiClient(keep)
    notes = client.list_notes()

    assert len(notes) == 1
    assert notes[0].name == "a"
    assert notes[0].title == "Active"


def test_get_note_returns_none_for_missing() -> None:
    client = GkeepApiClient(FakeKeep())
    assert client.get_note("nonexistent") is None


def test_get_note_converts_text_note() -> None:
    keep = FakeKeep()
    keep._notes["x"] = FakeNote(id="x", title="Hello", text="world")

    client = GkeepApiClient(keep)
    note = client.get_note("x")

    assert note is not None
    assert note.name == "x"
    assert note.kind == "text"
    assert note.text_body == "world"


def test_get_note_converts_list_note() -> None:
    keep = FakeKeep()
    child = FakeListItem(text="child", checked=True)
    parent = FakeListItem(text="parent", checked=False, _subitems=[child])
    keep._notes["lst"] = FakeList(id="lst", title="Tasks", items=[parent])

    client = GkeepApiClient(keep)
    note = client.get_note("lst")

    assert note is not None
    assert note.kind == "list"
    assert len(note.list_items) == 1
    assert note.list_items[0].text == "parent"
    assert note.list_items[0].children[0].text == "child"
    assert note.list_items[0].children[0].checked is True


def test_create_text_note_syncs() -> None:
    keep = FakeKeep()
    client = GkeepApiClient(keep)

    note = client.create_text_note("Title", "Body")

    assert note.kind == "text"
    assert note.title == "Title"
    assert keep.synced is True


def test_create_list_note_with_children() -> None:
    keep = FakeKeep()
    client = GkeepApiClient(keep)
    items = [
        ChecklistItem(text="parent", checked=False, children=[
            ChecklistItem(text="child", checked=True),
        ]),
    ]

    note = client.create_list_note("Checklist", items)

    assert note.kind == "list"
    assert keep.synced is True
    # The FakeList should have had indent() called via add()
    raw_list = keep._notes[note.name]
    assert isinstance(raw_list, FakeList)


def test_delete_note_trashes_and_syncs() -> None:
    keep = FakeKeep()
    keep._notes["d"] = FakeNote(id="d", title="Delete me")

    client = GkeepApiClient(keep)
    client.delete_note("d")

    assert keep._notes["d"].trashed is True
    assert keep.synced is True


def test_delete_note_ignores_missing() -> None:
    keep = FakeKeep()
    client = GkeepApiClient(keep)
    client.delete_note("missing")  # should not raise


def test_replace_text_note_creates_new_and_deletes_old() -> None:
    keep = FakeKeep()
    keep._notes["old"] = FakeNote(id="old", title="Old")

    client = GkeepApiClient(keep)
    new = client.replace_text_note("old", "New", "body")

    assert new.name != "old"
    assert new.title == "New"
    assert keep._notes["old"].trashed is True


def test_download_attachment_raises_without_cache() -> None:
    from kiko.markdown_model import KeepAttachment

    client = GkeepApiClient(FakeKeep())
    att = KeepAttachment(name="missing/blob/0", mime_types=["image/png"])

    with pytest.raises(KeepClientError, match="not found"):
        client.download_attachment(att, __import__("pathlib").Path("/tmp/x"))
