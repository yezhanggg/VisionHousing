# Slide deck

The submission slide deck (at most 30 slides, PDF). It is generated, not typed: every number
on a slide is read from the tool's own results and listed, with the file or address it came
from, in `numbers.json`.

| File | What it is |
|---|---|
| `VisionHousing_deck.pdf` | The deck to submit: one 16:9 slide per page |
| `VisionHousing_deck.html` | The same slides as one web page (open it to review) |
| `numbers.json` | Every number on every slide, and its source file or address |
| `deck.css` | The slide styles; colours and type come from `web/css/tokens.css` |
| `screens/` | Six pictures of the running tool (1920 x 1080 JPEG, each under 400 KB) for the Usability slide |

The words live in `scripts/build_deck.py`, one block per slide, in order.

## Rebuild it

```
make export-static STATE=PA      # refresh the saved results first, when numbers change
uv run python scripts/build_deck.py --api http://127.0.0.1:8000
```

`--api` is optional. Without it the deck uses only the saved files in `web/public/data/`;
cards those files do not hold yet (evidence grades, the plausibility card, districts, the law
timeline) show a short "appears here once exported" note instead of a number. With it, the
running tool (`make app`) answers them. `--comparisons` also asks for the policy dials and the
data-ablation test, which is a long job on a large state.

`--screens` first photographs the running tool (`make app`, or the address in `--app`) with
headless Chromium: the State screen with ADU on, the same in 3D, the Reform decision view, the
Philadelphia County profile charts (`--county` picks another), the Method back-test map and
Compare. Compare runs every reform, so it can take minutes. The pictures are the tool's own
screens, so every number in them is the tool's own text, as of the run the tool was serving.
Take them again after the final export:

```
uv run python scripts/build_deck.py --screens --api http://127.0.0.1:8000
```

Without all six pictures the Usability slide falls back to short text cards.

A slide whose numbers come from a saved run made before the final export says so in its
footer. The build stops if the deck has more than 30 slides or the PDF more than 30 pages.
The test `tests/test_deck.py` builds the deck from Testland, offline.
