"""Conservative candidate matching, not an official function registry or an LLM substitute."""

import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations

import numpy as np

from app.extraction import normalize

ALGORITHM = "document-candidates-v1"
CATEGORIES = {
    "unchanged": "Формулировка сохранена",
    "possible_rephrasing": "Возможное переформулирование",
    "possible_transfer": "Возможная передача между подразделениями",
    "possible_duplication": "Повторяющаяся формулировка",
    "split": "Возможное разделение формулировки",
    "merge": "Возможное объединение формулировок",
    "changed_content": "Изменение текста или границ ответственности",
    "new_text": "Новая формулировка в комплекте",
    "documented_abolition": "В тексте указано об исключении функции",
    "not_found": "Соответствие не найдено в проверенном корпусе",
    "conflict": "Возможное противоречие формулировок",
    "insufficient_data": "Недостаточно данных",
}


def words(value):
    return set(re.findall(r"\w+", normalize(value)))


def mentions_abolition(value):
    return bool(re.search(r"(?:упраздн|исключ\w*\s+функц|отмен\w*\s+функц)", value))


def compare(before, after, before_vectors, after_vectors, limitations):
    findings, used_before, used_after = [], set(), set()
    btext, atext = [normalize(x["quote"]) for x in before], [normalize(x["quote"]) for x in after]
    bw, aw = [words(x) for x in btext], [words(x) for x in atext]
    similarity = (
        np.asarray(before_vectors) @ np.asarray(after_vectors).T if before and after else None
    )

    def add(category, bi, ai, explanation, method="exact_text", provenance="deterministic"):
        findings.append(
            {
                "category": category,
                "title": CATEGORIES[category],
                "before_ids": [before[i]["anchor_id"] for i in bi],
                "after_ids": [after[i]["anchor_id"] for i in ai],
                "explanation": explanation,
                "method": method,
                "provenance": provenance,
                "limitations": list(limitations)
                + (
                    []
                    if category == "unchanged"
                    else [
                        "Кандидат для проверки человеком; официальные функции и назначения не изменяются."
                    ]
                ),
            }
        )
        used_before.update(bi)
        used_after.update(ai)

    exact = defaultdict(list)
    for j, value in enumerate(atext):
        exact[value].append(j)
    for i, value in enumerate(btext):
        matches = exact.get(value, [])
        if not matches:
            continue
        if len(matches) > 1:
            # Bounded cards retain all repetitions across several findings if necessary.
            for first in range(0, len(matches), 8):
                add(
                    "possible_duplication",
                    [i],
                    matches[first : first + 8],
                    "Одинаковый текст встречается в нескольких местах нового комплекта. Повтор сам по себе не доказывает конфликт или дублирование ответственности.",
                )
        else:
            j = matches[0]
            category = (
                "possible_transfer"
                if before[i]["department_id"] != after[j]["department_id"]
                else "unchanged"
            )
            add(
                category,
                [i],
                [j],
                "Нормализованный текст совпадает. Проверьте редакцию, применимость, исполнение и контроль; совпадение текста не утверждает передачу полномочий.",
            )

    # Detect textual split/merge before one-to-one semantic candidates. Evaluate actual joined text.
    def group_matches(
        single, many, single_words, many_words, single_used, many_used, reverse=False
    ):
        for i, value in enumerate(single):
            if i in single_used or len(value) < 35:
                continue
            candidates = [
                j
                for j in range(len(many))
                if j not in many_used
                and 0.18 * len(value) <= len(many[j]) <= 0.85 * len(value)
                and len(single_words[i] & many_words[j]) / max(len(many_words[j]), 1) > 0.65
            ]
            candidates = sorted(
                candidates, key=lambda j: (-len(single_words[i] & many_words[j]), j)
            )[:8]
            for pair in combinations(sorted(candidates), 2):
                combined = " ".join(many[j] for j in pair)
                if SequenceMatcher(None, value, combined, autojunk=False).ratio() >= 0.90:
                    add(
                        "merge" if reverse else "split",
                        list(pair) if reverse else [i],
                        [i] if reverse else list(pair),
                        "Сцепленный текст двух фрагментов близок исходному (правило сравнения строк ≥ 0,90). Смысл и границы ответственности проверяются вручную.",
                        "joined_text_ratio>=0.90",
                    )
                    break

    group_matches(btext, atext, bw, aw, used_before, used_after)
    group_matches(atext, btext, aw, bw, used_after, used_before, True)

    for i, value in enumerate(btext):
        if i in used_before:
            continue
        candidates = [j for j in range(len(after)) if j not in used_after]
        scores = []
        for j in candidates:
            lexical = len(bw[i] & aw[j]) / max(len(bw[i] | aw[j]), 1)
            semantic = float(similarity[i, j])
            scores.append((0.45 * lexical + 0.55 * semantic, j, lexical, semantic))
        scores.sort(key=lambda row: (-row[0], row[1]))
        if scores:
            _, j, lexical, semantic = scores[0]
            if semantic >= 0.88 or lexical >= 0.45:
                ratio = SequenceMatcher(None, value, atext[j], autojunk=False).ratio()
                negation_changed = bool(re.search(r"\bне\b", value)) != bool(
                    re.search(r"\bне\b", atext[j])
                )
                category = (
                    "conflict"
                    if negation_changed and lexical >= 0.65
                    else "documented_abolition"
                    if mentions_abolition(atext[j]) and not mentions_abolition(value)
                    else "possible_transfer"
                    if before[i]["department_id"] != after[j]["department_id"]
                    else "possible_rephrasing"
                    if semantic >= 0.93 and ratio < 0.8
                    else "changed_content"
                )
                add(
                    category,
                    [i],
                    [j],
                    "Найден кандидат по словам и локальным векторам. Это сходство формулировок, не вероятность истинности и не подтверждение изменения функции.",
                    "token_jaccard+local_cosine; full_selected_corpus",
                    "local_semantic_candidate",
                )
                continue
        category = "insufficient_data" if limitations or not after else "not_found"
        add(
            category,
            [i],
            [],
            "Проверен весь зафиксированный новый корпус по точному тексту, словам и локальным векторам. Подходящий кандидат по правилам алгоритма не найден. Это не доказательство отсутствия или упразднения функции; требуется сверка полного комплекта и реестра.",
            "full_corpus_candidate_scan",
            "local_semantic_candidate",
        )
    for j, value in enumerate(atext):
        if j not in used_after:
            category = "documented_abolition" if mentions_abolition(value) else "new_text"
            add(
                category,
                [],
                [j],
                "В новом комплекте осталась формулировка без установленного соответствия. Проверьте документальный статус и контекст; новая формулировка не равна новой официальной функции.",
                "unmatched_after_full_corpus",
            )
    if not findings:
        add(
            "insufficient_data",
            [],
            [],
            "В зафиксированных комплектах нет доступного извлечённого текста. Пустой корпус не является доказательством отсутствия функций.",
        )
    return findings


def text_diff(before, after):
    left, right = re.findall(r"\s+|\S+", before), re.findall(r"\s+|\S+", after)
    result = []
    for tag, a, b, c, d in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if tag == "equal":
            result.append({"kind": "equal", "text": "".join(left[a:b])})
        else:
            if a != b:
                result.append({"kind": "delete", "text": "".join(left[a:b])})
            if c != d:
                result.append({"kind": "insert", "text": "".join(right[c:d])})
    return result
