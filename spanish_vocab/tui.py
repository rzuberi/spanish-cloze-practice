import curses
import random
import textwrap


class SpanishAdventureApp(object):
    def __init__(self, engine):
        self.engine = engine
        self.quit_requested = False
        self.study_limit = 15

    def run(self):
        curses.wrapper(self._main)

    def _main(self, stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        curses.start_color()
        curses.use_default_colors()
        self._init_colors()
        while not self.quit_requested:
            action = self._dashboard(stdscr)
            if action == "study":
                self._study_loop(stdscr)
            elif action == "path":
                self._path_screen(stdscr)
            elif action == "recent":
                self._recent_reviews_screen(stdscr)
            elif action == "quit":
                self.quit_requested = True

    def _init_colors(self):
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_BLUE, -1)

    def _color(self, pair, bold=False):
        attr = curses.color_pair(pair)
        if bold:
            attr |= curses.A_BOLD
        return attr

    def _draw_title(self, stdscr, title, subtitle=None):
        height, width = stdscr.getmaxyx()
        stdscr.attron(self._color(1, True))
        stdscr.addnstr(1, 2, title, width - 4)
        stdscr.attroff(self._color(1, True))
        if subtitle:
            stdscr.attron(self._color(4))
            stdscr.addnstr(2, 2, subtitle, width - 4)
            stdscr.attroff(self._color(4))
        stdscr.hline(3, 2, ord("-"), max(0, width - 4))

    def _bar(self, width, value):
        fill = int(round((max(0.0, min(100.0, value)) / 100.0) * width))
        return "[" + ("#" * fill) + ("-" * max(0, width - fill)) + "]"

    def _wrap_by_words(self, text, words_per_line, max_width):
        words = text.split()
        if not words:
            return [""]
        lines = []
        index = 0
        while index < len(words):
            chunk = words[index : index + words_per_line]
            line = " ".join(chunk)
            if len(line) > max_width:
                lines.extend(textwrap.wrap(line, max_width))
            else:
                lines.append(line)
            index += words_per_line
        return lines

    def _safe_add(self, stdscr, y, x, text, attr=0):
        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        if x >= width:
            return
        stdscr.addnstr(y, x, text, max(0, width - x - 1), attr)

    def _format_time(self, iso_value):
        if not iso_value:
            return "never"
        clean = iso_value.replace("T", " ").replace("Z", " UTC")
        return clean

    def _dashboard(self, stdscr):
        dashboard = self.engine.get_dashboard()
        stdscr.erase()
        self._draw_title(
            stdscr,
            "Spanish Adventure",
            "Terminal-first Spanish training over SSH. [S]tudy  [P]ath  [R]ecent  [Q]uit",
        )
        active = dashboard["active_section"]
        if active:
            active_name = active["name"]
            active_progress = int(round(active["avg_mastery"] or 0.0))
        else:
            active_name = "All sections cleared"
            active_progress = 100

        lines = [
            "Last studied : %s" % self._format_time(dashboard["last_reviewed_at"]),
            "Study streak : %s day(s)" % dashboard["study_streak"],
            "Cards due    : %s" % dashboard["due_cards"],
            "New cards    : %s" % dashboard["new_cards"],
            "Mastered     : %s / %s" % (dashboard["mastered_cards"], dashboard["total_cards"]),
            "Adventure XP : %0.1f%%" % dashboard["avg_mastery"],
            "Current arc  : %s" % active_name,
            "Arc progress : %s %s%%" % (self._bar(24, active_progress), active_progress),
        ]
        y = 5
        for index, line in enumerate(lines):
            attr = 0
            if index in (5, 7):
                attr = self._color(2, True)
            self._safe_add(stdscr, y + index, 4, line, attr)

        y += len(lines) + 2
        self._safe_add(stdscr, y, 2, "Campaign map", self._color(5, True))
        y += 1
        available_rows = max(1, curses.LINES - y - 3)
        path_rows = self.engine.get_section_path()
        for idx, section in enumerate(path_rows[:available_rows]):
            prefix = "->" if section["state"] == "current" else " *"
            if section["state"] == "cleared":
                attr = self._color(2)
            elif section["state"] == "current":
                attr = self._color(4, True)
            else:
                attr = self._color(6)
            text = "%s %02d. %-32s %3d/%-3d  %5.1f%%" % (
                prefix,
                idx + 1,
                section["name"][:32],
                section["mastered"],
                section["total"],
                section["avg_mastery"],
            )
            self._safe_add(stdscr, y + idx, 4, text, attr)

        if len(path_rows) > available_rows:
            self._safe_add(
                stdscr,
                y + available_rows,
                4,
                "... %d more sections in the campaign path." % (len(path_rows) - available_rows),
                self._color(6),
            )

        footer = "Press S to study the next session."
        self._safe_add(stdscr, curses.LINES - 2, 2, footer, self._color(1))
        stdscr.refresh()
        while True:
            ch = stdscr.getch()
            if ch in (ord("s"), ord("S")):
                return "study"
            if ch in (ord("p"), ord("P")):
                return "path"
            if ch in (ord("r"), ord("R")):
                return "recent"
            if ch in (ord("q"), ord("Q")):
                return "quit"

    def _path_screen(self, stdscr):
        path = self.engine.get_section_path()
        offset = 0
        while True:
            stdscr.erase()
            self._draw_title(stdscr, "Campaign Path", "[Up/Down] scroll  [B]ack")
            view_height = curses.LINES - 6
            for idx, section in enumerate(path[offset : offset + view_height]):
                y = 5 + idx
                marker = "[x]" if section["state"] == "cleared" else "[>]" if section["state"] == "current" else "[ ]"
                attr = self._color(2) if section["state"] == "cleared" else self._color(4, True) if section["state"] == "current" else 0
                bar = self._bar(18, section["avg_mastery"])
                text = "%s %-34s %s %5.1f%% (%d/%d)" % (
                    marker,
                    section["name"][:34],
                    bar,
                    section["avg_mastery"],
                    section["mastered"],
                    section["total"],
                )
                self._safe_add(stdscr, y, 2, text, attr)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (ord("b"), ord("B"), 27):
                return
            if ch == curses.KEY_DOWN and offset + view_height < len(path):
                offset += 1
            if ch == curses.KEY_UP and offset > 0:
                offset -= 1

    def _recent_reviews_screen(self, stdscr):
        reviews = self.engine.get_recent_reviews(curses.LINES - 8)
        stdscr.erase()
        self._draw_title(stdscr, "Recent Reviews", "[B]ack")
        y = 5
        if not reviews:
            self._safe_add(stdscr, y, 2, "No reviews yet. Start a study session first.", self._color(4))
        for review in reviews:
            attr = self._color(2) if review["was_correct"] else self._color(3)
            line = "%s  %-24s  %-20s  you: %-16s  target: %s" % (
                review["reviewed_at"][:19],
                review["section_name"][:24],
                review["prompt"][:20],
                review["answer_given"][:16],
                review["answer"][:20],
            )
            self._safe_add(stdscr, y, 2, line, attr)
            y += 1
        stdscr.refresh()
        while True:
            ch = stdscr.getch()
            if ch in (ord("b"), ord("B"), 27):
                return

    def _study_loop(self, stdscr):
        self._loading_message(stdscr, "Finding sentence cards for this run...")
        queue, prefetched_examples = self.engine.get_sentence_session_queue(self.study_limit)
        if not queue:
            self._message(
                stdscr,
                "No sentence cards are ready right now. Try again in a moment or after the cache warms up.",
                self._color(4, True),
            )
            return

        batches = [queue[index : index + 5] for index in range(0, len(queue), 5)]
        for batch_index, batch in enumerate(batches, 1):
            sentence_examples = {}
            for card in batch:
                sentence_examples[card["id"]] = prefetched_examples.get(card["id"])
            result = self._run_batch(stdscr, batch, batch_index, len(batches), sentence_examples)
            if result == "quit":
                return
        self._message(stdscr, "Session complete. Dashboard updated.", self._color(2, True))

    def _run_batch(self, stdscr, batch, batch_index, batch_total, sentence_examples):
        hits = {card["id"]: 0 for card in batch}
        while True:
            pending = [card for card in batch if hits[card["id"]] < 3]
            if not pending:
                return "next"
            random.shuffle(pending)
            for card in pending:
                result = self._study_card(
                    stdscr,
                    card,
                    batch_index,
                    batch_total,
                    hits,
                    sentence_examples.get(card["id"]),
                    len(batch),
                )
                if result == "quit":
                    return "quit"
                if result == "correct":
                    hits[card["id"]] += 1

    def _study_card(self, stdscr, card, batch_index, batch_total, hits, sentence_example, batch_size):
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            if height < 16 or width < 40:
                self._message(
                    stdscr,
                    "Terminal too small for study mode. Resize to at least 40x16 and try again.",
                    self._color(3, True),
                )
                return "quit"
            cleared = len([card_id for card_id, count in hits.items() if count >= 3])
            local_hits = hits[card["id"]]
            subtitle = "Batch %d/%d  |  Cleared: %d/%d  |  This card: %d/3  |  Section: %s" % (
                batch_index,
                batch_total,
                cleared,
                batch_size,
                local_hits,
                card["section_name"],
            )
            self._draw_title(stdscr, "Study Run", subtitle)
            self._safe_add(stdscr, 5, 2, card["prompt"], self._color(5, True))

            content_y = 7
            content_lines = []
            if sentence_example:
                wrapped = self._wrap_by_words(sentence_example["cloze_text"], 6, max(20, width - 6))
                for line in wrapped:
                    content_lines.append((line, self._color(1, True)))
                if sentence_example.get("translation_text"):
                    content_lines.append(("", 0))
                    english_lines = self._wrap_by_words(sentence_example["translation_text"], 6, max(20, width - 6))
                    for line in english_lines:
                        content_lines.append((line, self._color(4)))
            else:
                content_lines.append(("Sentence example missing for this card.", self._color(3, True)))

            instruction_y = height - 4
            input_y = height - 2
            max_content_lines = max(1, instruction_y - content_y - 1)
            visible_lines = content_lines[:max_content_lines]
            if len(content_lines) > max_content_lines and visible_lines:
                line, attr = visible_lines[-1]
                visible_lines[-1] = ((line[: max(0, width - 10)] + " ...").strip(), attr)

            for offset, (line, attr) in enumerate(visible_lines):
                self._safe_add(stdscr, content_y + offset, 4, line, attr)

            self._safe_add(
                stdscr,
                instruction_y,
                2,
                "Type your answer and press Enter. [Esc] returns to dashboard.",
                self._color(4),
            )
            answer = self._capture_input(stdscr, input_y, 4, min(50, max(12, width - 8)))
            if answer is None:
                return "quit"

            correct, exact = self.engine.check_answer(card, answer)
            updated = self.engine.review_card(card["id"], answer, correct, exact=exact, sentence_example=sentence_example)
            self._show_feedback(stdscr, updated, answer, correct, exact)
            return "correct" if correct else "wrong"

    def _capture_input(self, stdscr, y, x, max_chars):
        buffer = []
        curses.curs_set(1)
        while True:
            height, width = stdscr.getmaxyx()
            y = max(0, min(y, height - 1))
            x = max(0, min(x, max(0, width - 1)))
            stdscr.move(y, x)
            display = "".join(buffer)
            self._safe_add(stdscr, y, x, (" " * max_chars))
            self._safe_add(stdscr, y, x, display[:max_chars])
            stdscr.refresh()
            ch = stdscr.get_wch()
            if ch in ("\n", "\r") or ch == curses.KEY_ENTER:
                curses.curs_set(0)
                return "".join(buffer).strip()
            if ch == "\x1b":
                curses.curs_set(0)
                return None
            if ch in ("\b", "\x7f") or ch == curses.KEY_BACKSPACE:
                if buffer:
                    buffer.pop()
                continue
            if isinstance(ch, str) and ch.isprintable() and len(buffer) < max_chars:
                buffer.append(ch)

    def _show_feedback(self, stdscr, card, answer, correct, exact):
        stdscr.erase()
        if correct:
            self._draw_title(stdscr, "Correct", "Good retrieval. Interval increased.")
            status_attr = self._color(2, True)
            status_text = "Accepted"
            if not exact:
                status_text = "Accepted (accent/article forgiving mode)"
        else:
            self._draw_title(stdscr, "Not Quite", "The card stays in the loop and will come back sooner.")
            status_attr = self._color(3, True)
            status_text = "Miss"

        self._safe_add(stdscr, 5, 2, "Result      : %s" % status_text, status_attr)
        self._safe_add(stdscr, 7, 2, "Your answer : %s" % (answer or "(blank)"), self._color(4))
        self._safe_add(stdscr, 8, 2, "Target      : %s" % card["answer"], self._color(1, True))
        self._safe_add(stdscr, 10, 2, "Mastery     : %0.1f%%" % card["mastery"], self._color(2))
        self._safe_add(stdscr, 11, 2, "Next review : %0.2f day(s)" % card["interval_days"], self._color(2))
        self._safe_add(stdscr, 12, 2, "Streak      : %d" % card["streak"], self._color(2))
        self._safe_add(stdscr, 13, 2, "Difficulty  : %0.2f" % card["difficulty"], self._color(4))
        if correct:
            self._safe_add(stdscr, curses.LINES - 2, 2, "Auto-advancing...", self._color(1))
        else:
            self._safe_add(stdscr, curses.LINES - 2, 2, "Press any key for the retry loop.", self._color(1))
        stdscr.refresh()
        if correct:
            stdscr.timeout(500)
            stdscr.getch()
            stdscr.timeout(-1)
        else:
            stdscr.getch()

    def _message(self, stdscr, text, attr):
        stdscr.erase()
        self._draw_title(stdscr, "Spanish Adventure", None)
        self._safe_add(stdscr, 6, 4, text, attr)
        self._safe_add(stdscr, curses.LINES - 2, 2, "Press any key to continue.", self._color(1))
        stdscr.refresh()
        stdscr.getch()

    def _loading_message(self, stdscr, text):
        stdscr.erase()
        self._draw_title(stdscr, "Study Run", "Preparing sentence mode")
        self._safe_add(stdscr, 6, 4, text, self._color(4, True))
        self._safe_add(stdscr, 8, 4, "Loading from the local sentence cache...", self._color(1))
        stdscr.refresh()
