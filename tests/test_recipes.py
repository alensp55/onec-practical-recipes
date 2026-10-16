from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def relative_hierarchy(nodes: list[dict[str, object]], root: str) -> list[dict[str, object]]:
    children: dict[str, list[str]] = {}
    known = set()
    for node in nodes:
        key = str(node["key"])
        known.add(key)
        children.setdefault(str(node["parent"]), []).append(key)
    if root not in known:
        return []
    result = []
    queue = [(root, "", 0)]
    visited = set()
    while queue:
        key, parent, level = queue.pop(0)
        if key in visited:
            raise ValueError("cycle")
        visited.add(key)
        result.append({"key": key, "parent": parent, "level": level})
        queue.extend((child, key, level + 1) for child in children.get(key, []))
    return result


def new_blocks(current: list[str], previous: list[str]) -> list[str]:
    previous_normalized = {item.strip().replace("\r\n", "\n") for item in previous}
    result = []
    added = set()
    for item in current:
        normalized = item.strip().replace("\r\n", "\n")
        if normalized and normalized not in previous_normalized and normalized not in added:
            result.append(item)
            added.add(normalized)
    return result


def state_transition(current: str, expected: str, next_state: str) -> dict[str, object]:
    if current == next_state:
        return {"execute": False, "code": "УжеВыполнено"}
    if current != expected:
        return {"execute": False, "code": "Конфликт"}
    return {"execute": True, "code": "Выполнить"}


def split_batches(values: list[str], batch_size: int) -> list[list[str]]:
    if batch_size <= 0:
        raise ValueError("batch size")
    return [values[index:index + batch_size] for index in range(0, len(values), batch_size)]


def can_claim(state: dict[str, object] | None, owner: str, now: datetime) -> bool:
    if state is None or state["status"] == "Ожидает":
        return True
    if state["status"] != "ВРаботе":
        return False
    lease_until = datetime.fromisoformat(str(state["leaseUntil"]))
    return state["owner"] == owner or lease_until <= now


def page_plan(keys: list[str], current: str | None, page_size: int) -> dict[str, object]:
    if page_size <= 0 or len(keys) > page_size:
        raise ValueError("page size")
    previous = current
    for key in keys:
        if not key or (previous is not None and key <= previous):
            raise ValueError("page order")
        previous = key
    return {"continue": len(keys) == page_size, "cursor": previous}


def prepare_result(rows: list[dict[str, object]], fields: list[str], version: str) -> dict[str, object]:
    if not fields or len(fields) != len(set(fields)) or any(not field for field in fields):
        raise ValueError("fields")
    prepared_rows = []
    for row in rows:
        if any(field not in row for field in fields):
            raise ValueError("missing field")
        prepared_rows.append({field: row[field] for field in fields})
    return {"version": version, "count": len(prepared_rows), "rows": prepared_rows}


class RecipeTests(unittest.TestCase):
    def test_relative_hierarchy_example(self) -> None:
        path = ROOT / "recipes" / "relative-hierarchy" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["expected"], relative_hierarchy(data["nodes"], data["root"]))

    def test_multiline_dedup_example(self) -> None:
        path = ROOT / "recipes" / "multiline-log-dedup" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["unambiguousExpected"], new_blocks(data["current"], data["previous"]))

    def test_safe_refill_example(self) -> None:
        path = ROOT / "recipes" / "safe-document-refill" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        incoming_keys = {row["key"] for row in data["incoming"]}
        actual = [row for row in data["existing"] if row["key"] not in incoming_keys] + data["incoming"]
        self.assertEqual(data["expected"], actual)

    def test_transactional_state_change_example(self) -> None:
        path = ROOT / "recipes" / "transactional-state-change" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data["cases"]:
            actual = state_transition(case["current"], case["expected"], case["next"])
            self.assertEqual(case["result"], actual)

    def test_batched_query_example(self) -> None:
        path = ROOT / "recipes" / "batched-query" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["expected"], split_batches(data["values"], data["batchSize"]))

    def test_batching_rejects_invalid_size_and_accepts_empty_input(self) -> None:
        self.assertEqual([], split_batches([], 10))
        with self.assertRaises(ValueError):
            split_batches(["a"], 0)

    def test_claim_lease_example(self) -> None:
        path = ROOT / "recipes" / "claim-lease-queue" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        now = datetime.fromisoformat(data["now"])
        for case in data["cases"]:
            self.assertEqual(case["expected"], can_claim(case["state"], data["owner"], now))

    def test_paged_selection_example(self) -> None:
        path = ROOT / "recipes" / "paged-selection-processing" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        cursor = None
        processed = 0
        continuation_cursors = []
        for page in data["pages"]:
            plan = page_plan(page, cursor, data["pageSize"])
            processed += len(page)
            if not plan["continue"]:
                break
            cursor = str(plan["cursor"])
            continuation_cursors.append(cursor)
        actual = {"processed": processed, "continuationCursors": continuation_cursors}
        self.assertEqual(data["expected"], actual)

    def test_page_plan_rejects_non_progressing_keys(self) -> None:
        with self.assertRaises(ValueError):
            page_plan(["b", "b"], "a", 2)
        with self.assertRaises(ValueError):
            page_plan(["a"], "b", 2)
        with self.assertRaises(ValueError):
            page_plan(["a"], None, 0)

    def test_temporary_storage_preparation_example(self) -> None:
        path = ROOT / "recipes" / "temporary-storage-transfer" / "example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        actual = prepare_result(data["rows"], data["fields"], data["version"])
        self.assertEqual(data["expected"], actual)

    def test_result_preparation_rejects_invalid_contract(self) -> None:
        with self.assertRaises(ValueError):
            prepare_result([], [], "1")
        with self.assertRaises(ValueError):
            prepare_result([{"code": "A"}], ["code", "caption"], "1")

    def test_every_recipe_has_documentation_and_bsl(self) -> None:
        directories = [directory for directory in (ROOT / "recipes").iterdir() if directory.is_dir()]
        self.assertGreaterEqual(len(directories), 8)
        for directory in directories:
            self.assertTrue((directory / "README.md").is_file(), directory.name)
            self.assertTrue((directory / "example.json").is_file(), directory.name)
            self.assertTrue(any(directory.glob("*.bsl")), directory.name)

    def test_new_recipes_expose_testable_functions(self) -> None:
        expected = {
            "transactional-state-change/StateTransition.bsl": {"ПроверитьПереход"},
            "batched-query/BatchQuery.bsl": {"РазбитьНаПакеты"},
            "claim-lease-queue/ClaimLeaseQueue.bsl": {"МожноЗахватить", "НовоеСостояниеАренды"},
            "safe-document-refill/SafeRefill.bsl": {"ОпределитьДействие", "ЗаменитьЗатронутыеСтроки"},
            "paged-selection-processing/PagedSelection.bsl": {"ПланСледующейСтраницы"},
            "temporary-storage-transfer/PrepareTemporaryResult.bsl": {"ПодготовитьРезультат"},
        }
        pattern = re.compile(r"^Функция\s+(\S+)\([^\r\n]*\)\s+Экспорт$", re.MULTILINE)
        for relative_path, required_functions in expected.items():
            source = (ROOT / "recipes" / relative_path).read_text(encoding="utf-8")
            self.assertTrue(required_functions.issubset(set(pattern.findall(source))), relative_path)

    def test_temporary_storage_isolated_from_pure_preparation(self) -> None:
        directory = ROOT / "recipes" / "temporary-storage-transfer"
        preparation = (directory / "PrepareTemporaryResult.bsl").read_text(encoding="utf-8")
        adapter = (directory / "TemporaryStorageAdapter.bsl").read_text(encoding="utf-8")
        platform_calls = {
            "ПоместитьВоВременноеХранилище",
            "ПолучитьИзВременногоХранилища",
            "УдалитьИзВременногоХранилища",
        }
        for call in platform_calls:
            self.assertNotIn(call, preparation)
            self.assertIn(call, adapter)

    def test_bsl_line_length_and_private_markers(self) -> None:
        forbidden = {"internal.example", "production_database", "customer_name", "localhost", "srvr=", "ref="}
        for path in ROOT.rglob("*.bsl"):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                self.assertLessEqual(len(line), 120, f"{path}:{number}")
            folded = text.casefold()
            for marker in forbidden:
                self.assertNotIn(marker, folded, str(path))

    def test_recipe_assets_do_not_contain_private_markers(self) -> None:
        forbidden_patterns = [
            re.compile(r"\b(?:Srvr|Ref)=", re.IGNORECASE),
            re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
            re.compile(r"\b[A-Za-z]:\\"),
            re.compile(r"\b(?:feature|bugfix|hotfix)/[\w.-]+", re.IGNORECASE),
        ]
        for path in (ROOT / "recipes").rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                self.assertIsNone(pattern.search(text), str(path))


if __name__ == "__main__":
    unittest.main()
