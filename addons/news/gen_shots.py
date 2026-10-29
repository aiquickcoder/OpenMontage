"""Flow illustrations for a news story (Nano Banana Pro, falls back to Nano Banana 2; free account, no video credits).

    .venv/bin/python addons/news/gen_shots.py projects/<slug>/story.json projects/<slug>/illustrations [ids...]

story.json: "illustration_style" (one paragraph, prepended to every prompt) and "illustrations":
[{"id": "c1", "story": 2, "beat": "<the phrase it goes under>", "show": "<what that phrase shows, one concrete moment>",
"frame": "wide|medium|detail", "agencies": ["police", ...], "mood": "...", "refs": ["media/place.jpg"]}].
With "show" the prompt is composed (owner 2026-10-29, credible pictures; README «Достоверность»): the style, the
story's "facts" card (place, time, season), the shot, the agencies as they really look (AGENCY), the rule on people
and on lettering — so every picture of a story keeps one place and one light. A hand-written "prompt" still works.
"refs" (paths relative to the project) go to Flow as reference images. One 16:9 picture per id -> <out>/<id>.png;
existing files are skipped, so a redraw
is: delete the file, run again with that id. Exit code 3 = daily quota on both models (see addons/mural/gen_panels.py).
Run it on the server (the Flow driver lives there) in the background; it prints DONE at the end.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from addons.mural import gen_panels as g  # noqa: E402

# owner 2026-10-29: a prompt asking for «a sports hall sign» got «СПОРТИВНЫЙ КОМПЛЕКС» — no lettering but quoted words
NO_TEXT = ("No text anywhere: no signs, no shop or street names, no writing on buildings, no house numbers; the only "
           "lettering allowed is a word quoted above on a uniform or a vehicle.")


AGENCY = {  # as they look in Russia (README «Ведомства — как в жизни»)
    "police": "Russian police patrol officers in dark-blue uniforms with reflective stripes and the word «ПОЛИЦИЯ» on the "
              "back; police cars are white Lada Vesta or UAZ with a blue stripe, «ПОЛИЦИЯ» on the doors and blue strobes",
    "rosgvardia": "Rosgvardia officers in black uniforms and body armour with «РОСГВАРДИЯ» on the back; grey-and-white "
                  "Rosgvardia patrol cars",
    "dps": "traffic police officers in lime-green reflective vests marked «ДПС», a white-and-blue patrol car",
    "mchs": "firefighters in dark-blue turnout gear with yellow reflective stripes, red fire engines marked «МЧС»",
    "ambulance": "a white Russian ambulance van with a red stripe and «03», paramedics in dark-blue uniforms",
    "sk": "Investigative Committee investigators in plain dark coats or dark-blue jackets marked «СК»",
    "fsb": "FSB operatives in plain clothes and black balaclavas, unmarked dark cars",
    "court": "a Russian courtroom: light wood panelling, the Russian coat of arms on the wall, a judge in a black robe, "
             "a glass enclosure for the defendant",
    "fsin": "prison service officers in grey uniforms, a white prisoner transport van",
    "bailiffs": "court bailiffs in dark uniforms with «ФССП» patches",
    "customs": "Russian customs officers in grey-blue uniforms",
}
FRAMES = {"wide": "Wide shot that shows the whole place.", "medium": "Medium shot of the action.",
          "detail": "Close-up detail shot."}
PEOPLE = ("Anyone involved is seen from behind, in silhouette or out of focus — never a recognisable face of a victim or a "
          "suspect; no blood, no injuries, no weapon aimed at anyone.")


def facts_text(facts) -> str:
    if isinstance(facts, dict):
        return " ".join(f"{str(facts[k])[:1].upper()}{str(facts[k])[1:].rstrip('.')}."
                        for k in ("place", "time", "season") if facts.get(k))
    return str(facts or "")


def prompt(story: dict, ill: dict) -> str:
    """The full prompt of one illustration (see the module doc)."""
    style = story["illustration_style"]
    if "show" not in ill:
        return f"{style} {ill['prompt']} {NO_TEXT} {g.SINGLE} {g.FRAME['16:9']}"
    stories = story.get("stories") or [story]
    st = stories[int(ill.get("story", 1)) - 1]
    unknown = [a for a in ill.get("agencies", []) if a not in AGENCY]
    if unknown:
        print(f"{ill['id']}: unknown agencies {unknown} (known: {', '.join(AGENCY)})", flush=True)
    parts = [style, facts_text(st.get("facts")), ill["show"].strip().rstrip(".") + ".", FRAMES[ill.get("frame", "medium")],
             *(AGENCY[a] + "." for a in ill.get("agencies", []) if a in AGENCY), ill.get("mood", ""), PEOPLE, NO_TEXT,
             g.SINGLE, g.FRAME["16:9"]]
    return " ".join(x for x in parts if x)


def main() -> None:
    story = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    g._load_state(out)
    only = set(sys.argv[3:])
    for s in story["illustrations"]:
        if only and s["id"] not in only:
            continue
        refs = [str((Path(sys.argv[1]).parent / r).resolve()) for r in s.get("refs", [])]
        g.draw(out, s["id"], prompt(story, s), refs, "16:9", 1)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
