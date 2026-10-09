"""Render the original 509 style with owner-supplied PNG assets.

Returns None when PNG assets are absent, allowing bot.py to use its
existing programmatic fallback. Canvas slots deliberately remain dynamic.
"""
from io import BytesIO
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets" / "rank"

def render_asset_rank_card(display_name, avatar_bytes, xp, placement,
                           message_count, joined_label, equipped_rank_number=None,
                           ranks=()):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    bg = ASSETS / "backgrounds" / "full_card_template_reference.png"
    if not bg.is_file():
        return None
    name, floor, _ = max((r for r in ranks if xp >= r[1]), key=lambda r:r[1])
    number = next(i for i, r in enumerate(ranks, 1) if r[0] == name)
    chosen = equipped_rank_number or number
    if not isinstance(chosen, int) or not 1 <= chosen <= 8:
        chosen = number
    badge_files = sorted((ASSETS / "badges").glob(f"rank_{chosen:02d}_*.png"))
    if not badge_files:
        return None
    canvas = Image.open(bg).convert("RGBA")
    if canvas.size != (2172, 724):
        return None
    draw = ImageDraw.Draw(canvas)
    white = (243,247,255,255)
    muted = (160,184,213,255)
    fpath = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    def font(sz):
        try:
            return ImageFont.truetype(fpath, sz)
        except OSError:
            return ImageFont.load_default()
    # Original reference contains empty slots on 2172x724 background.
    # Avatar frame interior approximately x=113..432,y=128..418.
    try:
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = ImageOps.fit(avatar, (304, 280), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (304, 280))
        ImageDraw.Draw(mask).rounded_rectangle((0,0,303,279), radius=16, fill=255)
        canvas.paste(avatar, (118,135), mask)
    except (OSError, ValueError, TypeError):
        pass
    # Badge placement overlays the empty shield slot.
    badge = Image.open(badge_files[0]).convert("RGBA")
    badge.thumbnail((204,204), Image.Resampling.LANCZOS)
    canvas.alpha_composite(badge, (580 - badge.width//2, 334 - badge.height//2))
    # Fit username and rank text without overlapping neighboring regions.
    def fitted(txt, limit, size):
        txt = str(txt)
        while size > 22 and draw.textbbox((0,0), txt, font=font(size))[2] > limit:
            size -= 2
        return font(size)
    draw.text((495,102), str(display_name)[:42], font=fitted(display_name,720,52), fill=white)
    draw.text((2065,68), f"#{placement}",font=font(58),anchor="ra",fill=white)
    draw.text((705,260),"RANK",font=font(30),fill=muted)
    draw.text((705,304),name,font=fitted(name,475,54),fill=white)
    draw.text((1215,255),"XP",font=font(32),fill=(103,169,255,255))
    next_rank = next((r for r in ranks if r[1] > xp),None)
    target = next_rank[1] if next_rank else xp
    draw.text((1215,297), f"{xp:,} / {target:,}" if next_rank else f"{xp:,} / MAX",
              font=font(36),fill=white)
    ratio = min(1.0,max(0.0,(xp-floor)/(target-floor))) if next_rank else 1.0
    # Track is baked into the image; only fill the inner portion.
    # Align the dynamic fill inside the baked track, not across its left border.
    bar_x,bar_y,bar_w,bar_h = 1210,390,785,43
    if ratio>0:
        width=max(1,round(bar_w*ratio))
        draw.rounded_rectangle((bar_x,bar_y,bar_x+width,bar_y+bar_h),
                               radius=min(16,width//2),fill=(245,56,73,255) if number==8 else (35,110,255,255))
    draw.text((2090,390),f"{round(ratio*100)}%",font=font(30),anchor="ra",fill=white)
    draw.text((218,530),f"{message_count:,} messages",font=fitted(f"{message_count:,} messages",365,36),fill=white)
    draw.text((850,530),f"Server Rank: #{placement}",font=font(34),fill=white)
    draw.text((1535,530),f"Joined {joined_label}",font=fitted(f"Joined {joined_label}",430,34),fill=white)
    output = BytesIO()
    # Render at source resolution and publish a Discord-friendly 1086x362 card.
    canvas.convert("RGB").resize((1086, 362), Image.Resampling.LANCZOS).save(output,format="PNG")
    output.seek(0)
    return output
