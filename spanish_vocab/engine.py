import datetime as _dt
import fcntl
import math
import os

from .db import (
    default_db_path,
    ensure_database,
    import_vocab_directory,
    normalize_answer,
    utc_now,
)
from .local_corpus import LocalTatoebaCorpus
from .paths import user_data_dir


class SpanishEngine(object):
    def __init__(self, package_dir, app_dir=None):
        self.package_dir = package_dir
        self.app_dir = app_dir or user_data_dir()
        self.vocab_dir = os.path.join(package_dir, "vocab")
        self.db_path = default_db_path(self.app_dir)
        self.conn = ensure_database(self.db_path)
        self.local_corpus = LocalTatoebaCorpus(self.app_dir)
        import_vocab_directory(self.conn, self.vocab_dir)
        self._ensure_sentence_cache_version()

    def close(self):
        self.conn.close()

    def _ensure_sentence_cache_version(self):
        cache_version = "v5_local_tatoeba"
        row = self._fetchone("SELECT value FROM meta WHERE key = 'sentence_example_format'")
        if row and row["value"] == cache_version:
            return
        self.conn.execute("DELETE FROM sentence_examples")
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('sentence_example_format', ?)",
            (cache_version,),
        )
        self.conn.commit()

    def _fetchone(self, query, params=()):
        row = self.conn.execute(query, params).fetchone()
        return row

    def _fetchall(self, query, params=()):
        return self.conn.execute(query, params).fetchall()

    def get_dashboard(self):
        now_iso = utc_now()
        totals = self._fetchone(
            """
            SELECT
                COUNT(*) AS total_cards,
                SUM(CASE WHEN due_at <= ? THEN 1 ELSE 0 END) AS due_cards,
                SUM(CASE WHEN seen_count = 0 THEN 1 ELSE 0 END) AS new_cards,
                SUM(CASE WHEN mastery >= 95 THEN 1 ELSE 0 END) AS mastered_cards,
                AVG(mastery) AS avg_mastery
            FROM cards
            """,
            (now_iso,),
        )
        last_review = self._fetchone("SELECT MAX(reviewed_at) AS reviewed_at FROM reviews")
        section_rows = self.get_section_stats()
        active = None
        for section in section_rows:
            if section["mastered_cards"] < section["total_cards"]:
                active = section
                break
        if active is None and section_rows:
            active = section_rows[-1]
        return {
            "total_cards": totals["total_cards"] or 0,
            "due_cards": totals["due_cards"] or 0,
            "new_cards": totals["new_cards"] or 0,
            "mastered_cards": totals["mastered_cards"] or 0,
            "avg_mastery": totals["avg_mastery"] or 0.0,
            "last_reviewed_at": last_review["reviewed_at"] if last_review else None,
            "study_streak": self.get_study_streak(),
            "active_section": active,
            "sections": section_rows,
        }

    def get_section_stats(self):
        return self._fetchall(
            """
            SELECT
                s.id,
                s.name,
                s.sort_order,
                s.total_cards,
                ROUND(AVG(c.mastery), 1) AS avg_mastery,
                SUM(CASE WHEN c.mastery >= 95 THEN 1 ELSE 0 END) AS mastered_cards,
                SUM(CASE WHEN c.due_at <= ? THEN 1 ELSE 0 END) AS due_cards,
                SUM(CASE WHEN c.seen_count = 0 THEN 1 ELSE 0 END) AS new_cards
            FROM sections s
            JOIN cards c ON c.section_id = s.id
            GROUP BY s.id, s.name, s.sort_order, s.total_cards
            ORDER BY s.sort_order ASC, s.name ASC
            """,
            (utc_now(),),
        )

    def get_study_streak(self):
        rows = self._fetchall(
            """
            SELECT DISTINCT substr(reviewed_at, 1, 10) AS review_day
            FROM reviews
            ORDER BY review_day DESC
            """
        )
        if not rows:
            return 0
        days = [row["review_day"] for row in rows]
        today = _dt.datetime.utcnow().date()
        streak = 0
        for offset in range(len(days) + 1):
            target = today - _dt.timedelta(days=offset)
            if target.isoformat() in days:
                streak += 1
            else:
                if offset == 0 and days and days[0] == (today - _dt.timedelta(days=1)).isoformat():
                    continue
                break
        return streak

    def get_section_path(self):
        sections = self.get_section_stats()
        path = []
        current_id = None
        for section in sections:
            mastered = int(section["mastered_cards"] or 0)
            total = int(section["total_cards"] or 0)
            average = float(section["avg_mastery"] or 0.0)
            state = "locked"
            if mastered >= total and total > 0:
                state = "cleared"
            elif current_id is None and total > 0:
                state = "current"
                current_id = section["id"]
            elif current_id is not None:
                state = "upcoming"
            path.append(
                {
                    "id": section["id"],
                    "name": section["name"],
                    "mastered": mastered,
                    "total": total,
                    "avg_mastery": average,
                    "state": state,
                }
            )
        return path

    def get_session_queue(self, limit):
        now_iso = utc_now()
        due_cards = self._fetchall(
            """
            SELECT c.*, s.name AS section_name, s.sort_order AS section_order
            FROM cards c
            JOIN sections s ON s.id = c.section_id
            WHERE c.due_at <= ?
            ORDER BY s.sort_order ASC, c.due_at ASC, c.mastery ASC, c.source_order ASC
            LIMIT ?
            """,
            (now_iso, limit),
        )
        if len(due_cards) >= limit:
            return due_cards

        active = self.get_dashboard()["active_section"]
        if not active:
            return due_cards

        needed = limit - len(due_cards)
        new_cards = self._fetchall(
            """
            SELECT c.*, s.name AS section_name, s.sort_order AS section_order
            FROM cards c
            JOIN sections s ON s.id = c.section_id
            WHERE c.section_id = ? AND c.seen_count = 0
            ORDER BY c.source_order ASC
            LIMIT ?
            """,
            (active["id"], needed),
        )
        existing = {row["id"] for row in due_cards}
        queue = list(due_cards)
        for row in new_cards:
            if row["id"] not in existing:
                queue.append(row)
        return queue

    def get_sentence_session_queue(self, limit):
        candidate_limit = max(limit * 12, 72)
        candidates = self.get_session_queue(candidate_limit)
        chosen = []
        examples = {}
        for card in candidates:
            example = self.get_sentence_example(card, fetch_if_missing=False)
            if not example:
                continue
            chosen.append(card)
            examples[card["id"]] = example
            if len(chosen) >= limit:
                break
        if len(chosen) >= limit:
            return chosen, examples

        fetch_budget = max(limit * 4, 18)
        attempts = 0
        for card in candidates:
            if card["id"] in examples:
                continue
            example = self.get_sentence_example(card, fetch_if_missing=True)
            attempts += 1
            if not example:
                if attempts >= fetch_budget and chosen:
                    break
                continue
            chosen.append(card)
            examples[card["id"]] = example
            if len(chosen) >= limit:
                break
        return chosen, examples

    def check_answer(self, card, answer):
        supplied = normalize_answer(answer)
        target = card["normalized_answer"]
        exact = answer.strip().lower() == card["answer"].strip().lower()
        return supplied == target, exact

    def review_card(self, card_id, answer, was_correct, exact=False, sentence_example=None):
        card = self._fetchone("SELECT * FROM cards WHERE id = ?", (card_id,))
        if not card:
            raise ValueError("Unknown card id: %s" % card_id)

        now = _dt.datetime.utcnow().replace(microsecond=0)
        seen_count = int(card["seen_count"] or 0) + 1
        correct_count = int(card["correct_count"] or 0) + (1 if was_correct else 0)
        lapse_count = int(card["lapse_count"] or 0) + (0 if was_correct else 1)
        streak = int(card["streak"] or 0)
        mastery = float(card["mastery"] or 0.0)
        difficulty = float(card["difficulty"] or 2.5)
        interval_days = float(card["interval_days"] or 0.0)

        if was_correct:
            streak += 1
            retention_ratio = float(correct_count) / float(seen_count)
            difficulty = max(1.15, difficulty - 0.08)
            if interval_days < 1.0:
                interval_days = 1.0
            else:
                growth = 1.45 + ((retention_ratio - 0.5) * 0.9) + ((100.0 - difficulty * 20.0) / 200.0)
                interval_days = max(interval_days * growth, interval_days + 1.0, float(streak))
            mastery_gain = 10.0 + min(22.0, math.log(interval_days + 1.0, 2) * 6.0)
            mastery = min(100.0, mastery + mastery_gain)
            result = "correct"
        else:
            streak = 0
            difficulty = min(5.0, difficulty + 0.35)
            interval_days = max(0.15, interval_days * 0.35)
            mastery = max(0.0, mastery - max(12.0, mastery * 0.25))
            result = "wrong"

        due_at = now + _dt.timedelta(days=interval_days)
        now_iso = now.isoformat() + "Z"
        due_iso = due_at.isoformat() + "Z"

        self.conn.execute(
            """
            UPDATE cards
            SET seen_count = ?,
                correct_count = ?,
                streak = ?,
                lapse_count = ?,
                mastery = ?,
                difficulty = ?,
                interval_days = ?,
                last_result = ?,
                last_answer = ?,
                last_reviewed_at = ?,
                due_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                seen_count,
                correct_count,
                streak,
                lapse_count,
                mastery,
                difficulty,
                interval_days,
                result,
                answer,
                now_iso,
                due_iso,
                now_iso,
                card_id,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO reviews (
                card_id,
                reviewed_at,
                was_correct,
                answer_given,
                mastery_after,
                interval_after,
                expected_answer,
                normalized_expected_answer,
                is_exact,
                sentence_text,
                cloze_text,
                translation_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card_id,
                now_iso,
                1 if was_correct else 0,
                answer,
                mastery,
                interval_days,
                card["answer"],
                card["normalized_answer"],
                1 if exact else 0,
                sentence_example["sentence_text"] if sentence_example else None,
                sentence_example["cloze_text"] if sentence_example else None,
                sentence_example["translation_text"] if sentence_example else None,
            ),
        )
        self.conn.commit()

        updated = self._fetchone(
            """
            SELECT c.*, s.name AS section_name, s.sort_order AS section_order
            FROM cards c
            JOIN sections s ON s.id = c.section_id
            WHERE c.id = ?
            """,
            (card_id,),
        )
        return updated

    def get_recent_reviews(self, limit):
        return self._fetchall(
            """
            SELECT r.reviewed_at, r.was_correct, r.answer_given, c.prompt, c.answer, s.name AS section_name
            FROM reviews r
            JOIN cards c ON c.id = r.card_id
            JOIN sections s ON s.id = c.section_id
            ORDER BY r.reviewed_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    def warm_sentence_examples(self, cards):
        examples = {}
        for card in cards:
            try:
                examples[card["id"]] = self.get_sentence_example(card)
            except Exception:
                examples[card["id"]] = None
        return examples

    def get_sentence_example(self, card, fetch_if_missing=True):
        cached = self._fetchone(
            "SELECT sentence_text, cloze_text, translation_text, source, fetched_at FROM sentence_examples WHERE card_id = ?",
            (card["id"],),
        )
        if cached:
            if cached["source"] == "none":
                self.conn.execute("DELETE FROM sentence_examples WHERE card_id = ?", (card["id"],))
                self.conn.commit()
            else:
                return dict(cached)

        if not fetch_if_missing:
            return None

        example = self._find_local_sentence_example(card)
        now_iso = utc_now()
        if example:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO sentence_examples
                    (card_id, sentence_text, cloze_text, translation_text, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    card["id"],
                    example["sentence_text"],
                    example["cloze_text"],
                    example["translation_text"],
                    "tatoeba",
                    now_iso,
                ),
            )
            self.conn.commit()
            return example
        return None

    def get_cache_snapshot(self):
        total = self._fetchone("SELECT COUNT(*) AS total FROM sentence_examples")
        active = self._fetchone(
            """
            SELECT COUNT(*) AS total
            FROM sentence_examples se
            JOIN cards c ON c.id = se.card_id
            WHERE c.mastery < 95
            """
        )
        return {
            "total": total["total"] if total else 0,
            "active": active["total"] if active else 0,
        }

    def prefetch_sentence_cache(self, low_water=30, high_water=60, candidate_limit=320, max_attempts=36):
        lock_handle = self._acquire_prefetch_lock()
        if not lock_handle:
            return {"status": "busy", "filled": 0, "active_cache": self.get_cache_snapshot()["active"]}

        try:
            self._prune_sentence_cache(candidate_limit)
            snapshot = self.get_cache_snapshot()
            active_cache = snapshot["active"]
            if active_cache >= low_water:
                return {"status": "ready", "filled": 0, "active_cache": active_cache}

            candidates = self.get_session_queue(candidate_limit)
            filled = 0
            attempts = 0
            for card in candidates:
                if self.get_sentence_example(card, fetch_if_missing=False):
                    continue
                attempts += 1
                example = self.get_sentence_example(card, fetch_if_missing=True)
                if example:
                    filled += 1
                snapshot = self.get_cache_snapshot()
                if snapshot["active"] >= high_water:
                    break
                if attempts >= max_attempts:
                    break
            snapshot = self.get_cache_snapshot()
            return {"status": "filled", "filled": filled, "active_cache": snapshot["active"]}
        finally:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()

    def build_local_corpus(self):
        self.local_corpus.build()

    def install_local_data(self, stream=None, rebuild=False):
        self.local_corpus.ensure_ready(stream=stream, rebuild=rebuild)

    def local_corpus_ready(self):
        return self.local_corpus.exists()

    def _prune_sentence_cache(self, candidate_limit):
        self.conn.execute(
            """
            DELETE FROM sentence_examples
            WHERE card_id IN (
                SELECT id FROM cards WHERE mastery >= 95
            )
            """
        )
        candidates = self.get_session_queue(candidate_limit)
        keep_ids = [card["id"] for card in candidates]
        if keep_ids:
            placeholders = ",".join(["?"] * len(keep_ids))
            self.conn.execute(
                "DELETE FROM sentence_examples WHERE card_id NOT IN (%s)" % placeholders,
                tuple(keep_ids),
            )
        self.conn.commit()

    def _acquire_prefetch_lock(self):
        lock_dir = os.path.join(self.app_dir, "data")
        if not os.path.isdir(lock_dir):
            os.makedirs(lock_dir)
        lock_path = os.path.join(lock_dir, "sentence_prefetch.lock")
        handle = open(lock_path, "w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            handle.close()
            return None
        return handle

    def _find_local_sentence_example(self, card):
        answer = card["answer"].strip()
        spanish_candidates = []
        articleless = answer
        parts = answer.split(None, 1)
        if parts and parts[0].lower() in ("el", "la", "los", "las", "un", "una", "unos", "unas") and len(parts) > 1:
            articleless = parts[1].strip()

        for candidate in (answer, articleless, normalize_answer(answer)):
            candidate = candidate.strip()
            if candidate and candidate not in spanish_candidates:
                spanish_candidates.append(candidate)

        english_candidates = []
        prompt = card["prompt"].strip()
        if prompt:
            english_candidates.append(prompt.lower())
        if prompt.lower().startswith("to "):
            bare = prompt[3:].strip()
            if bare and bare not in english_candidates:
                english_candidates.append(bare.lower())

        if not self.local_corpus.exists():
            return None
        return self.local_corpus.find_example(english_candidates, spanish_candidates)
