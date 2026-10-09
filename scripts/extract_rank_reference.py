"""Extract original crops of the SideBot reference illustration, with no redesign.

Run: python scripts/extract_rank_reference.py <source.png> <output-directory>
The source must be the 1757x895 reference supplied by the server owner.
These crops are reference materials with baked-in visual elements.
"""
from pathlib import Path
from PIL import Image
import argparse
import json

RANKS = ["NPC", "Random", "Side Character", "Regular", "Main Character", "Certified", "GOAT", "Himothy"]
BADGE_X = [(25,221),(231,429),(437,638),(645,846),(856,1056),(1065,1263),(1271,1468),(1477,1675)]
CARDS = {
    "npc": (25,75,843,320),
    "main_character": (856,75,1672,319),
    "side_character": (25,332,844,579),
    "himothy": (855,333,1672,579),
}

def extract(source, output):
    image = Image.open(source).convert("RGBA")
    if image.size != (1757, 895):
        raise ValueError(f"Unexpected reference size: {image.size}, expected 1757x895")
    out = Path(output)
    for directory in ("badges", "reference_cards", "components"):
        (out / directory).mkdir(parents=True, exist_ok=True)
    for number, (rank, (left, right)) in enumerate(zip(RANKS, BADGE_X), 1):
        slug = rank.lower().replace(" ", "_")
        image.crop((left, 651, right, 836)).save(out / "badges" / f"{number:02}_{slug}_reference_tile.png")
        mid = (left + right) // 2
        image.crop((mid - 66, 651, mid + 67, 786)).save(out / "badges" / f"{number:02}_{slug}_emblem_crop.png")
    for name, box in CARDS.items():
        image.crop(box).save(out / "reference_cards" / f"{name}.png")
    sample = image.crop(CARDS["main_character"])
    sample.save(out / "components" / "source_card_full.png")
    sample.crop((0, 0, sample.width, 180)).save(out / "components" / "source_card_header_baked.png")
    sample.crop((0, 184, sample.width, sample.height)).save(out / "components" / "source_card_footer_baked.png")
    with (out / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump({"source": "The 509 exact reference screenshot", "ranks": RANKS,
                   "layer_status": "reference pixel crops, NOT isolated transparent production assets"}, fh, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    arguments = parser.parse_args()
    extract(arguments.source, arguments.output)
