from __future__ import print_function

import os
import re
import sqlite3
import tarfile
import tempfile
import urllib.request

from .db import normalize_text


RAW_DIRNAME = "tatoeba/raw"
CORPUS_DB_FILENAME = "tatoeba_local.db"
EXPORT_BASE_URL = "https://downloads.tatoeba.org/exports"


def corpus_paths(base_dir):
    data_dir = os.path.join(base_dir, "data", "tatoeba")
    raw_dir = os.path.join(base_dir, "data", RAW_DIRNAME)
    corpus_db = os.path.join(data_dir, CORPUS_DB_FILENAME)
    return data_dir, raw_dir, corpus_db


class LocalTatoebaCorpus(object):
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.data_dir, self.raw_dir, self.db_path = corpus_paths(app_dir)

    def raw_files_exist(self):
        return os.path.isfile(self._sentences_archive()) and os.path.isfile(self._links_archive())

    def exists(self):
        return os.path.isfile(self.db_path)

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def download_raw_exports(self, stream=None):
        self._ensure_dir(self.raw_dir)
        downloads = [
            ("sentences.tar.bz2", self._sentences_archive()),
            ("links.tar.bz2", self._links_archive()),
        ]
        for filename, path in downloads:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                continue
            url = "%s/%s" % (EXPORT_BASE_URL, filename)
            self._write_status(stream, "Downloading %s..." % filename)
            self._download_file(url, path)
        self._write_status(stream, "Tatoeba exports ready at %s" % self.raw_dir)

    def ensure_ready(self, stream=None, rebuild=False):
        if rebuild and os.path.isfile(self.db_path):
            os.unlink(self.db_path)
        if not self.raw_files_exist():
            self.download_raw_exports(stream=stream)
        if not self.exists():
            self._write_status(stream, "Building local Tatoeba corpus...")
            self.build()
            self._write_status(stream, "Local corpus ready at %s" % self.db_path)

    def build(self):
        self._ensure_dir(self.data_dir)
        temp_fd, temp_path = tempfile.mkstemp(prefix="tatoeba_local_", suffix=".db", dir=self.data_dir)
        os.close(temp_fd)
        try:
            conn = sqlite3.connect(temp_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._create_schema(conn)
            eng_ids, spa_ids = self._load_sentences(conn)
            self._load_links(conn, eng_ids, spa_ids)
            conn.execute("INSERT INTO eng_fts(rowid, text) SELECT id, text FROM eng_sentences")
            conn.commit()
            conn.close()
            os.replace(temp_path, self.db_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def find_example(self, prompt_variants, spanish_variants, limit_per_prompt=40):
        if not self.exists():
            return None
        conn = self.connect()
        try:
            for prompt in prompt_variants:
                prompt = prompt.strip()
                if not prompt:
                    continue
                prompt_norm = normalize_text(prompt)
                rows = self._search_english_rows(conn, prompt, prompt_norm, limit_per_prompt)
                for row in rows:
                    if prompt_norm not in row["norm_text"]:
                        continue
                    translations = conn.execute(
                        """
                        SELECT s.text, s.norm_text
                        FROM translation_pairs p
                        JOIN spa_sentences s ON s.id = p.spa_id
                        WHERE p.eng_id = ?
                        LIMIT 24
                        """,
                        (row["id"],),
                    ).fetchall()
                    for trans in translations:
                        for candidate in spanish_variants:
                            cloze = build_cloze(trans["text"], candidate)
                            if cloze:
                                return {
                                    "sentence_text": trans["text"],
                                    "cloze_text": cloze,
                                    "translation_text": row["text"],
                                }
        finally:
            conn.close()
        return None

    def _search_english_rows(self, conn, prompt, prompt_norm, limit_per_prompt):
        rows = []
        query = phrase_query(prompt)
        if query:
            try:
                rows = conn.execute(
                    """
                    SELECT e.id, e.text, e.norm_text
                    FROM eng_fts f
                    JOIN eng_sentences e ON e.id = f.rowid
                    WHERE eng_fts MATCH ?
                    LIMIT ?
                    """,
                    (query, limit_per_prompt),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if rows or not prompt_norm:
            return rows
        return conn.execute(
            """
            SELECT id, text, norm_text
            FROM eng_sentences
            WHERE norm_text LIKE ?
            LIMIT ?
            """,
            ("%% %s %%" % prompt_norm, limit_per_prompt),
        ).fetchall()

    def _create_schema(self, conn):
        conn.executescript(
            """
            CREATE TABLE eng_sentences (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                norm_text TEXT NOT NULL
            );
            CREATE TABLE spa_sentences (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                norm_text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE eng_fts USING fts5(text);
            CREATE TABLE translation_pairs (
                eng_id INTEGER NOT NULL,
                spa_id INTEGER NOT NULL,
                PRIMARY KEY (eng_id, spa_id)
            );
            CREATE INDEX idx_pairs_eng ON translation_pairs(eng_id);
            """
        )

    def _load_sentences(self, conn):
        eng_ids = set()
        spa_ids = set()
        eng_batch = []
        spa_batch = []
        with tarfile.open(self._sentences_archive(), "r:bz2") as archive:
            member = archive.getmember("sentences.csv")
            handle = archive.extractfile(member)
            for raw_line in handle:
                line = raw_line.decode("utf-8", "replace").rstrip("\n")
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                sentence_id = int(parts[0])
                lang = parts[1]
                text = parts[2]
                if lang == "eng":
                    eng_ids.add(sentence_id)
                    eng_batch.append((sentence_id, text, " %s " % normalize_text(text)))
                    if len(eng_batch) >= 5000:
                        conn.executemany("INSERT INTO eng_sentences (id, text, norm_text) VALUES (?, ?, ?)", eng_batch)
                        eng_batch = []
                elif lang == "spa":
                    spa_ids.add(sentence_id)
                    spa_batch.append((sentence_id, text, " %s " % normalize_text(text)))
                    if len(spa_batch) >= 5000:
                        conn.executemany("INSERT INTO spa_sentences (id, text, norm_text) VALUES (?, ?, ?)", spa_batch)
                        spa_batch = []
        if eng_batch:
            conn.executemany("INSERT INTO eng_sentences (id, text, norm_text) VALUES (?, ?, ?)", eng_batch)
        if spa_batch:
            conn.executemany("INSERT INTO spa_sentences (id, text, norm_text) VALUES (?, ?, ?)", spa_batch)
        conn.commit()
        return eng_ids, spa_ids

    def _load_links(self, conn, eng_ids, spa_ids):
        batch = []
        with tarfile.open(self._links_archive(), "r:bz2") as archive:
            member = archive.getmember("links.csv")
            handle = archive.extractfile(member)
            for raw_line in handle:
                line = raw_line.decode("utf-8", "replace").rstrip("\n")
                parts = line.split("\t")
                if len(parts) != 2:
                    continue
                left = int(parts[0])
                right = int(parts[1])
                pair = None
                if left in eng_ids and right in spa_ids:
                    pair = (left, right)
                elif left in spa_ids and right in eng_ids:
                    pair = (right, left)
                if pair:
                    batch.append(pair)
                    if len(batch) >= 20000:
                        conn.executemany("INSERT OR IGNORE INTO translation_pairs (eng_id, spa_id) VALUES (?, ?)", batch)
                        batch = []
        if batch:
            conn.executemany("INSERT OR IGNORE INTO translation_pairs (eng_id, spa_id) VALUES (?, ?)", batch)
        conn.commit()

    def _sentences_archive(self):
        return os.path.join(self.raw_dir, "sentences.tar.bz2")

    def _links_archive(self):
        return os.path.join(self.raw_dir, "links.tar.bz2")

    def _ensure_dir(self, path):
        if not os.path.isdir(path):
            os.makedirs(path)

    def _write_status(self, stream, message):
        if stream:
            stream.write(message + "\n")
            stream.flush()

    def _download_file(self, url, destination):
        response = urllib.request.urlopen(url, timeout=60)
        try:
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        finally:
            response.close()


def phrase_query(text):
    tokens = re.findall(r"[0-9A-Za-z]+(?:'[0-9A-Za-z]+)?", text.lower())
    if not tokens:
        return None
    return " AND ".join(['"%s"' % token.replace('"', "") for token in tokens])


def build_cloze(sentence, answer_text):
    needle = answer_text.strip()
    if not needle:
        return None
    pattern = re.compile(r"\b" + re.escape(needle) + r"\b", re.IGNORECASE)
    match = pattern.search(sentence)
    if not match:
        return None
    replacement = "_" * max(4, len(match.group(0)))
    return sentence[: match.start()] + replacement + sentence[match.end() :]
