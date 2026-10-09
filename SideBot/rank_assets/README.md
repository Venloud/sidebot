# The 509 rank-card artwork

The user-supplied 1757×895 reference image is the exact appearance target for the ranking system. The bot currently creates an approximate card with `draw_rank_card` in `SideBot/bot.py`; it does **not** yet render all source image layers faithfully.

## Original reference components

Eight badge artwork tiles, in order: NPC, Random, Side Character, Regular, Main Character, Certified, GOAT, Himothy. Four examples: NPC, Side Character, Main Character, Himothy. Card elements include background, Haitian flag / citadel / palm silhouette, avatar frame, username, rank label, progress bar, and footer count/position/joined information.

Use `python scripts/extract_rank_reference.py /path/to/reference.png output-dir` to reproduce original source crops from the screenshot. The user has also been supplied a ZIP of these extracted components.

**Caution:** The source is a flat screenshot. Pixel-exact crops contain baked-in labels/backgrounds; they are not clean, independent transparent layers. Don't use screenshot crops as a live template with duplicate existing usernames or XP values. The live bot still uses the older programmatic renderer until a clean layered background and separately masked badge sprites are available. Do not silently replace with new design artwork.

## Planned integration

1. Obtain/extract a clean base background and separate transparent badges preserving the exact reference styling.
2. Make a Pillow template renderer with fixed slots for avatar, username, rank, XP, progress, server placement, message count, and join date.
3. Keep the eight existing XP thresholds and Discord role mapping unchanged.
4. Test using all eight rank values, long usernames, high XP, and Discord avatars.
