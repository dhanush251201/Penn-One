"""Inline exported JSON into self-contained pages.

    python3 -m penn_twin.build_page          # replay + ops
    python3 -m penn_twin.build_page ops      # just the operator console
"""
import sys
from pathlib import Path

VIZ = Path(__file__).resolve().parent.parent / "viz"
PAGES = {"replay": ("replay_data.json", "replay.template.html", "replay.html"),
         "ops": ("ops_data.json", "ops.template.html", "ops.html")}


def build(name):
    data_f, tpl_f, out_f = PAGES[name]
    data = (VIZ / data_f).read_text().replace("</", "<\\/")
    html = (VIZ / tpl_f).read_text().replace("/*DATA*/", data)
    (VIZ / out_f).write_text(html)
    print(f"wrote {VIZ / out_f} ({len(html) / 1e3:.0f} kB)")


if __name__ == "__main__":
    for n in sys.argv[1:] or list(PAGES):
        build(n)
