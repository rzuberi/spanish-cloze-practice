# Spanish Cloze Practice

Spanish Cloze Practice is a terminal-first Spanish study game with spaced repetition and local sentence cards built from the Tatoeba English-Spanish corpus.

## Install

```bash
pip install git+https://github.com/rzuberi/spanish-cloze-practice.git
```

Then start it with:

```bash
spanish
```

On first launch, the app downloads the Tatoeba exports it needs and builds a local sentence database automatically.

## How it works

- runs in the terminal over SSH
- keeps progress in a local SQLite database
- studies in 15-card runs, grouped into batches of 5
- loops each batch until every card has been answered correctly 3 times
- uses spaced repetition so difficult cards come back sooner and stronger cards move further out
- uses full English sentences plus Spanish cloze sentences built from local Tatoeba data

## Commands

```bash
spanish
spanish --stats
spanish --path
spanish --install-data
spanish --build-local-corpus
```

## Local storage

By default, local data is stored in:

```text
~/.local/share/spanish-cloze-practice
```

You can override that location with:

```bash
export SPANISH_CLOZE_HOME=/path/to/your/data-dir
```
