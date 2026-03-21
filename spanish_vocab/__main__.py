from __future__ import print_function

import argparse
import subprocess
import sys

from .engine import SpanishEngine
from .paths import package_dir
from .tui import SpanishAdventureApp


def build_parser():
    parser = argparse.ArgumentParser(description="Spanish cloze practice in the terminal")
    parser.add_argument("--stats", action="store_true", help="print a plain-text dashboard")
    parser.add_argument("--path", action="store_true", help="print the campaign path")
    parser.add_argument("--prefetch", action="store_true", help="warm the sentence cache and exit")
    parser.add_argument("--install-data", action="store_true", help="download Tatoeba exports and build the local corpus")
    parser.add_argument("--build-local-corpus", action="store_true", help="rebuild the local Tatoeba sentence corpus")
    return parser


def print_stats(engine):
    dashboard = engine.get_dashboard()
    active = dashboard["active_section"]
    print("Spanish Adventure")
    print("DB: %s" % engine.db_path)
    print("Last studied : %s" % (dashboard["last_reviewed_at"] or "never"))
    print("Study streak : %s" % dashboard["study_streak"])
    print("Cards due    : %s" % dashboard["due_cards"])
    print("New cards    : %s" % dashboard["new_cards"])
    print("Mastered     : %s / %s" % (dashboard["mastered_cards"], dashboard["total_cards"]))
    print("Adventure XP : %0.1f%%" % dashboard["avg_mastery"])
    if active:
        print("Current arc  : %s (%0.1f%%)" % (active["name"], active["avg_mastery"] or 0.0))


def print_path(engine):
    for index, section in enumerate(engine.get_section_path(), 1):
        marker = "x" if section["state"] == "cleared" else ">" if section["state"] == "current" else "."
        print("%s %02d %-36s %3d/%-3d %5.1f%%" % (
            marker,
            index,
            section["name"][:36],
            section["mastered"],
            section["total"],
            section["avg_mastery"],
        ))


def maybe_start_prefetch():
    try:
        with open(os.devnull, "wb") as sink:
            subprocess.Popen(
                [sys.executable, "-m", "spanish_vocab", "--prefetch"],
                stdout=sink,
                stderr=sink,
                close_fds=True,
            )
    except Exception:
        return


def main():
    parser = build_parser()
    args = parser.parse_args()
    engine = SpanishEngine(package_dir())
    try:
        if args.stats:
            print_stats(engine)
            return
        if args.path:
            print_path(engine)
            return
        if args.install_data:
            engine.install_local_data(stream=sys.stdout)
            return
        if args.build_local_corpus:
            engine.install_local_data(stream=sys.stdout, rebuild=True)
            return
        if args.prefetch:
            if not engine.local_corpus_ready():
                engine.install_local_data(stream=sys.stdout)
            engine.prefetch_sentence_cache()
            return
        if not engine.local_corpus_ready():
            sys.stdout.write("First run setup: downloading Tatoeba data and building the local corpus.\n")
            sys.stdout.flush()
            engine.install_local_data(stream=sys.stdout)
        if engine.local_corpus_ready():
            maybe_start_prefetch()
        app = SpanishAdventureApp(engine)
        app.run()
    finally:
        engine.close()


if __name__ == "__main__":
    main()
