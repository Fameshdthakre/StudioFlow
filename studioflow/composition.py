"""Deterministic, versioned product-card layouts. No external API calls."""

import math
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

FONTS = Path(__file__).resolve().parents[1] / "resources" / "fonts"
TEMPLATES = ("feature", "dimensions", "pack_contents", "usage", "detail_collage")


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    template: Literal[
        "feature", "dimensions", "pack_contents", "usage", "detail_collage"
    ] = "feature"
    headline: str = Field(default="", max_length=100)
    body: str = Field(default="", max_length=260)
    theme: Literal["light", "dark"] = "light"
    heading_color: str = Field(default="#78150F", pattern=r"^#[0-9a-fA-F]{6}$")
    body_color: str = Field(default="#222222", pattern=r"^#[0-9a-fA-F]{6}$")
    facts_confirmed: bool = False
    measurement: float | None = Field(default=None, gt=0, le=100000)
    unit: Literal["cm", "mm", "m", "in"] = "cm"
    pack_count: int | None = Field(default=None, ge=1, le=12)
    cutout_asset_id: str | None = None
    cutout_approved: bool = False
    # A dimension arrow and pack arrangement need a verified single-item cutout.
    cutout_is_single_item: bool = False
    text_position: Literal["top_left", "top_right"] = "top_left"

    @model_validator(mode="after")
    def confirmed(self):
        if not self.headline.strip():
            raise ValueError("Enter a headline")
        if not self.facts_confirmed:
            raise ValueError("Confirm the text and product facts before composing")
        if self.template in ("dimensions", "pack_contents"):
            if (
                not self.cutout_asset_id
                or not self.cutout_approved
                or not self.cutout_is_single_item
            ):
                raise ValueError(
                    "This template requires an approved, single-item transparent cutout"
                )
        if self.template == "dimensions" and self.measurement is None:
            raise ValueError("Enter the verified product measurement")
        if self.template == "pack_contents" and self.pack_count is None:
            raise ValueError("Enter the verified pack quantity")
        # Check text fit before an accepted generation can incur a charge.
        probe = Image.new("RGB", (1, 1))
        draw_text(probe, self.headline, (0, 0, 890, 235), "heading", "#000000", 100, 48)
        draw_text(probe, self.body, (0, 0, 890, 165), "body", "#000000", 58, 38)
        return self


def font(name, size):
    path = FONTS / ("Cinzel.ttf" if name == "heading" else "Roboto.ttf")
    if not path.is_file():
        raise RuntimeError("Bundled layout font is missing")
    return ImageFont.truetype(str(path), size)


def wrap(draw, value, face, width):
    lines = []
    for paragraph in value.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            if draw.textlength(word, font=face) > width:
                return None
            candidate = (current + " " + word).strip()
            if current and draw.textlength(candidate, font=face) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return "\n".join(lines)


def draw_text(image, value, box, name, color, maximum, minimum):
    if not value.strip():
        return
    draw = ImageDraw.Draw(image)
    x, y, width, height = box
    for size in range(maximum, minimum - 1, -2):
        face = font(name, size)
        text = wrap(draw, value.strip(), face, width)
        if text is None:
            continue
        bounds = draw.multiline_textbbox(
            (0, 0), text, font=face, spacing=int(size * 0.24)
        )
        if bounds[3] - bounds[1] <= height:
            draw.multiline_text(
                (x, y - bounds[1]),
                text,
                font=face,
                fill=color,
                spacing=int(size * 0.24),
            )
            return
    raise ValueError("Text does not fit this layout; shorten the copy")


def place_cutout(image, cutout, box):
    x, y, width, height = map(int, box)
    with Image.open(cutout) as src:
        src = ImageOps.exif_transpose(src).convert("RGBA")
        if src.getchannel("A").getextrema()[0] == 255:
            raise ValueError("Use an approved transparent cutout")
        bounds = src.getchannel("A").getbbox()
        if not bounds:
            raise ValueError("Cutout is empty")
        src = src.crop(bounds)
        src = ImageOps.contain(src, (width, height), Image.Resampling.LANCZOS)
        px, py = x + (width - src.width) // 2, y + (height - src.height) // 2
        image.alpha_composite(src, (px, py))
        return px, py, src.width, src.height


def main_image(cutout, output, count=1):
    image = Image.new("RGBA", (2000, 2000), "white")
    if not 1 <= count <= 12:
        raise ValueError("Confirm a pack quantity between 1 and 12")
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    for index in range(count):
        cell_w, cell_h = 1760 / columns, 1760 / rows
        row_count = min(columns, count - (index // columns) * columns)
        offset = (columns - row_count) * cell_w / 2
        place_cutout(
            image,
            cutout,
            (
                160 + offset + index % columns * cell_w,
                160 + index // columns * cell_h,
                cell_w - 80,
                cell_h - 80,
            ),
        )
    image.convert("RGB").save(output, "JPEG", quality=95, optimize=True)


def compose(background, output, recipe, *, cutout=None):
    recipe = Recipe.model_validate(recipe)
    with Image.open(background) as source:
        # Fit without cropping product imagery. Layouts allocate their own regions.
        image = ImageOps.pad(
            ImageOps.exif_transpose(source).convert("RGBA"),
            (2000, 2000),
            color="#F9F5EC" if recipe.theme == "light" else "#102624",
        )
        if recipe.template == "usage":
            # A framed scene preserves the product below the caption instead
            # of covering it with the feature template's text overlay.
            fill = "#F9F5EC" if recipe.theme == "light" else "#102624"
            image = Image.new("RGBA", (2000, 2000), fill)
            scene = ImageOps.pad(
                ImageOps.exif_transpose(source).convert("RGBA"),
                (1840, 1260),
                color=fill,
            )
            image.alpha_composite(scene, (80, 660))
    panel = Image.new("RGBA", image.size)
    dark = recipe.theme == "dark"
    fill = (8, 24, 23, 230) if dark else (255, 253, 245, 235)
    draw = ImageDraw.Draw(panel)
    x = 100 if recipe.text_position == "top_left" else 950
    draw.rounded_rectangle((x - 40, 60, x + 950, 570), radius=28, fill=fill)
    image.alpha_composite(panel)
    heading = (
        "#E6D181"
        if dark and recipe.heading_color == "#78150F"
        else recipe.heading_color
    )
    body = "#FFFFFF" if dark and recipe.body_color == "#222222" else recipe.body_color
    draw_text(image, recipe.headline, (x, 110, 890, 235), "heading", heading, 100, 48)
    draw_text(image, recipe.body, (x, 365, 890, 165), "body", body, 58, 38)
    if recipe.template == "dimensions":
        if cutout is None:
            raise ValueError("Missing cutout")
        ImageDraw.Draw(image).rectangle((1080, 610, 1999, 1999), fill="#FFFEF8")
        _, top, _, height = place_cutout(image, cutout, (1130, 680, 620, 1190))
        draw = ImageDraw.Draw(image)
        draw.line((1850, top, 1850, top + height), fill="#A78B3E", width=7)
        for yy, direction in ((top, 1), (top + height, -1)):
            draw.polygon(
                [(1850, yy), (1828, yy + 40 * direction), (1872, yy + 40 * direction)],
                fill="#A78B3E",
            )
        middle = top + height // 2
        draw.rectangle((1740, middle - 60, 1980, middle + 60), fill="#FFFEF8")
        draw_text(
            image,
            f"{recipe.measurement:g} {recipe.unit}",
            (1760, middle - 40, 210, 90),
            "body",
            "#111111",
            52,
            32,
        )
    elif recipe.template == "pack_contents":
        if cutout is None:
            raise ValueError("Missing cutout")
        ImageDraw.Draw(image).rectangle((80, 650, 1920, 1920), fill="#FFFEF8")
        columns = math.ceil(math.sqrt(recipe.pack_count))
        rows = math.ceil(recipe.pack_count / columns)
        for index in range(recipe.pack_count):
            cw, ch = 1760 / columns, 1180 / rows
            row_count = min(columns, recipe.pack_count - (index // columns) * columns)
            offset = (columns - row_count) * cw / 2
            place_cutout(
                image,
                cutout,
                (
                    150 + offset + index % columns * cw,
                    720 + index // columns * ch,
                    cw - 60,
                    ch - 60,
                ),
            )
    elif recipe.template == "detail_collage":
        if cutout:
            ImageDraw.Draw(image).rounded_rectangle(
                (90, 1190, 810, 1910), radius=24, fill="#FFFEF8"
            )
            place_cutout(image, cutout, (120, 1220, 660, 660))
        else:
            with Image.open(background) as source:
                inset = ImageOps.fit(
                    source.convert("RGBA"), (650, 650), centering=(0.5, 0.5)
                )
            image.alpha_composite(inset, (120, 1230))
    image.convert("RGB").save(output, "JPEG", quality=95, optimize=True)


def thumbnail(source, output):
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        image.save(output, "WEBP", quality=82)
