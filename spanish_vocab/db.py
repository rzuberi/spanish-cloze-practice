import datetime as _dt
import os
import sqlite3
import unicodedata


DB_FILENAME = "spanish_adventure.db"


SECTION_ORDER = [
    "Beginner",
    "Common Phrases - All",
    "Greetings and Responses - All",
    "Questions and Question Words - All",
    "Numbers - All",
    "Colors - All",
    "Time and Date Expressions - All",
    "Daily Routine - All",
    "At Home - All",
    "Food and Drink - All",
    "Travel",
    "Travel - All",
    "People - All",
    "Family - All",
    "Feelings and Emotions - All",
    "Verbs - Top 50",
    "Verbs - Top 100",
    "Verbs - Top 250",
    "Verbs - All",
    "Prepositions - All",
    "Adverbs - All",
    "Adjectives - All",
    "Transition Words - All",
    "Intermediate",
    "Advanced",
]


CREATE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        sort_order INTEGER NOT NULL,
        total_cards INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL,
        prompt TEXT NOT NULL,
        answer TEXT NOT NULL,
        normalized_answer TEXT NOT NULL,
        source_order INTEGER NOT NULL,
        seen_count INTEGER NOT NULL DEFAULT 0,
        correct_count INTEGER NOT NULL DEFAULT 0,
        streak INTEGER NOT NULL DEFAULT 0,
        lapse_count INTEGER NOT NULL DEFAULT 0,
        mastery REAL NOT NULL DEFAULT 0,
        difficulty REAL NOT NULL DEFAULT 2.50,
        interval_days REAL NOT NULL DEFAULT 0,
        last_result TEXT,
        last_answer TEXT,
        last_reviewed_at TEXT,
        due_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(section_id, prompt, answer),
        FOREIGN KEY(section_id) REFERENCES sections(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id INTEGER NOT NULL,
        reviewed_at TEXT NOT NULL,
        was_correct INTEGER NOT NULL,
        answer_given TEXT NOT NULL,
        mastery_after REAL NOT NULL,
        interval_after REAL NOT NULL,
        FOREIGN KEY(card_id) REFERENCES cards(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentence_examples (
        card_id INTEGER PRIMARY KEY,
        sentence_text TEXT,
        cloze_text TEXT,
        translation_text TEXT,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        FOREIGN KEY(card_id) REFERENCES cards(id)
    )
    """,
]


def utc_now():
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def slugify(name):
    base = normalize_text(name)
    chars = []
    for char in base:
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-")


def normalize_text(value):
    value = value.strip().lower()
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = []
    previous_space = False
    for ch in stripped:
        if ch.isalnum():
            cleaned.append(ch)
            previous_space = False
        elif ch in " /-_":
            if not previous_space:
                cleaned.append(" ")
            previous_space = True
    normalized = " ".join("".join(cleaned).split())
    return normalized


def normalize_answer(value):
    words = normalize_text(value).split()
    articles = {"el", "la", "los", "las", "un", "una", "unos", "unas"}
    while words and words[0] in articles:
        words = words[1:]
    return " ".join(words)


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def default_db_path(base_dir):
    data_dir = os.path.join(base_dir, "data")
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)
    return os.path.join(data_dir, DB_FILENAME)


def ensure_database(db_path):
    conn = connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    for statement in CREATE_STATEMENTS:
        conn.execute(statement)
    _ensure_review_columns(conn)
    conn.commit()
    return conn


def _ensure_review_columns(conn):
    existing = set()
    for row in conn.execute("PRAGMA table_info(reviews)"):
        existing.add(row[1])

    needed = {
        "expected_answer": "TEXT",
        "normalized_expected_answer": "TEXT",
        "is_exact": "INTEGER NOT NULL DEFAULT 0",
        "sentence_text": "TEXT",
        "cloze_text": "TEXT",
        "translation_text": "TEXT",
    }
    for column, declaration in needed.items():
        if column not in existing:
            try:
                conn.execute("ALTER TABLE reviews ADD COLUMN %s %s" % (column, declaration))
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


def section_rank(name):
    if name in SECTION_ORDER:
        return SECTION_ORDER.index(name)
    return len(SECTION_ORDER) + 1000


def parse_vocab_line(line):
    raw = line.strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 2:
        return None
    prompt = parts[0]
    answer = parts[1]
    score = 0.0
    if len(parts) >= 3:
        try:
            score = float(parts[-1])
        except ValueError:
            score = 0.0
    return prompt, answer, score


def card_state_from_legacy_score(score, now_iso):
    score = max(0.0, score)
    score_bucket = int(round(score))
    if score_bucket <= 0:
        return {
            "seen_count": 0,
            "correct_count": 0,
            "streak": 0,
            "mastery": 0.0,
            "difficulty": 2.5,
            "interval_days": 0.0,
            "last_result": None,
            "last_reviewed_at": None,
            "due_at": now_iso,
        }

    interval_lookup = {
        1: 1.0,
        2: 3.0,
        3: 7.0,
        4: 14.0,
        5: 35.0,
        6: 60.0,
    }
    mastery_lookup = {
        1: 18.0,
        2: 38.0,
        3: 58.0,
        4: 78.0,
        5: 96.0,
        6: 100.0,
    }
    interval_days = interval_lookup.get(score_bucket, 60.0)
    reviewed_at = (_dt.datetime.utcnow() - _dt.timedelta(days=interval_days / 2.0)).replace(microsecond=0)
    due_at = reviewed_at + _dt.timedelta(days=interval_days)
    return {
        "seen_count": score_bucket,
        "correct_count": score_bucket,
        "streak": min(score_bucket, 6),
        "mastery": mastery_lookup.get(score_bucket, 100.0),
        "difficulty": max(1.3, 2.7 - (score_bucket * 0.18)),
        "interval_days": interval_days,
        "last_result": "correct",
        "last_reviewed_at": reviewed_at.isoformat() + "Z",
        "due_at": due_at.isoformat() + "Z",
    }


def import_vocab_directory(conn, vocab_dir):
    now_iso = utc_now()
    filenames = [name for name in os.listdir(vocab_dir) if name.endswith(".txt")]
    filenames.sort(key=lambda item: (section_rank(os.path.splitext(item)[0]), item.lower()))

    for position, filename in enumerate(filenames):
        name = os.path.splitext(filename)[0]
        slug = slugify(name)
        conn.execute(
            """
            INSERT INTO sections (name, slug, sort_order, total_cards, created_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(name) DO UPDATE SET sort_order = excluded.sort_order
            """,
            (name, slug, position, now_iso),
        )
        section_id = conn.execute("SELECT id FROM sections WHERE name = ?", (name,)).fetchone()["id"]
        total_cards = 0
        filepath = os.path.join(vocab_dir, filename)
        with open(filepath, "r", encoding="utf-8") as handle:
            for source_order, line in enumerate(handle):
                parsed = parse_vocab_line(line)
                if not parsed:
                    continue
                prompt, answer, score = parsed
                state = card_state_from_legacy_score(score, now_iso)
                conn.execute(
                    """
                    INSERT INTO cards (
                        section_id, prompt, answer, normalized_answer, source_order,
                        seen_count, correct_count, streak, lapse_count, mastery,
                        difficulty, interval_days, last_result, last_answer,
                        last_reviewed_at, due_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(section_id, prompt, answer) DO UPDATE SET
                        source_order = excluded.source_order,
                        normalized_answer = excluded.normalized_answer,
                        updated_at = excluded.updated_at
                    """,
                    (
                        section_id,
                        prompt,
                        answer,
                        normalize_answer(answer),
                        source_order,
                        state["seen_count"],
                        state["correct_count"],
                        state["streak"],
                        state["mastery"],
                        state["difficulty"],
                        state["interval_days"],
                        state["last_result"],
                        answer if state["last_result"] else None,
                        state["last_reviewed_at"],
                        state["due_at"],
                        now_iso,
                        now_iso,
                    ),
                )
                total_cards += 1
        conn.execute("UPDATE sections SET total_cards = ? WHERE id = ?", (total_cards, section_id))
    conn.commit()
