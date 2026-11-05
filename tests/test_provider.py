"""Offline provider contracts and product-preserving export regression tests."""

import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageDraw

from studioflow.provider import (
    ALLOWED_MODELS,
    DEFAULT_MODEL,
    MODEL_CONFIG,
    PRESETS,
    REVISION_MODEL,
    render_live,
    validate_native_size,
    validate_settings,
)


def image_response(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(buffer.getvalue()).decode())]
    )


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.source = self.base / "source.png"
        self.prior = self.base / "prior.jpg"
        self.output = self.base / "output.jpg"
        Image.new("RGB", (180, 180), "red").save(self.source)
        Image.new("RGB", (180, 180), "blue").save(self.prior)
        self.client = SimpleNamespace(
            images=SimpleNamespace(
                edit=Mock(
                    return_value=image_response(Image.new("RGB", (300, 200), "green"))
                )
            )
        )

    def render(self, **kwargs):
        values = {
            "inputs": [self.source],
            "output": self.output,
            "prompt": "Keep the exact product and change its background",
            "client": self.client,
        }
        values.update(kwargs)
        render_live(**values)

    def test_edit_contract_uses_source_and_supported_parameters(self):
        self.render()
        kwargs = self.client.images.edit.call_args.kwargs
        self.assertEqual(kwargs["model"], DEFAULT_MODEL)
        self.assertEqual(kwargs["quality"], "medium")
        self.assertEqual(kwargs["image"][0], self.source.name)
        self.assertEqual(kwargs["image"][1], self.source.read_bytes())
        self.assertEqual(kwargs["image"][2], "image/png")
        self.assertNotIn("response_format", kwargs)
        with Image.open(self.output) as output:
            self.assertEqual(output.format, "JPEG")
            self.assertEqual(output.size, PRESETS["square"][1])

    def test_revision_supplies_prior_and_original_in_order(self):
        self.render(
            inputs=[self.prior, self.source], model=REVISION_MODEL, quality="max"
        )
        kwargs = self.client.images.edit.call_args.kwargs
        self.assertEqual(
            [item[0] for item in kwargs["image"]], [self.prior.name, self.source.name]
        )
        self.assertEqual(
            [item[2] for item in kwargs["image"]], ["image/jpeg", "image/png"]
        )
        self.assertEqual(kwargs["quality"], "max")

    def test_banner_retains_top_and_bottom_product_details(self):
        image = Image.new("RGB", (300, 200), "green")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 299, 19), fill="red")
        draw.rectangle((0, 180, 299, 199), fill="blue")
        self.client.images.edit.return_value = image_response(image)
        self.render(canvas="banner")
        with Image.open(self.output) as output:
            self.assertEqual(output.size, (1464, 600))
            red = output.getpixel((732, 15))
            blue = output.getpixel((732, 585))
            white = output.getpixel((10, 300))
            self.assertGreater(red[0], 200)
            self.assertLess(red[1], 50)
            self.assertGreater(blue[2], 200)
            self.assertLess(blue[0], 50)
            self.assertTrue(all(channel > 240 for channel in white))

    def test_transparency_is_composited_on_white(self):
        self.client.images.edit.return_value = image_response(
            Image.new("RGBA", (100, 100), (255, 0, 0, 0))
        )
        self.render(canvas="mobile")
        with Image.open(self.output) as output:
            self.assertTrue(
                all(channel > 240 for channel in output.getpixel((300, 225)))
            )

    def test_invalid_models_and_quality_are_rejected_before_api_call(self):
        for model, quality in [("dall-e-3", "high"), (DEFAULT_MODEL, "hd")]:
            with (
                self.subTest(model=model, quality=quality),
                self.assertRaises(ValueError),
            ):
                self.render(model=model, quality=quality)
        self.client.images.edit.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_invalid_canvas_and_prompt_are_rejected_before_api_call(self):
        for kwargs in [
            {"canvas": "unsupported"},
            {"prompt": ""},
            {"prompt": "x" * 32001},
            {"inputs": []},
        ]:
            with self.subTest(kwargs=kwargs.keys()), self.assertRaises(ValueError):
                self.render(**kwargs)
        self.client.images.edit.assert_not_called()

    def test_missing_or_invalid_base64_never_overwrites_existing_asset(self):
        self.output.write_bytes(b"existing asset")
        responses = [
            SimpleNamespace(data=[]),
            SimpleNamespace(data=[SimpleNamespace(b64_json=None)]),
            SimpleNamespace(data=[SimpleNamespace(b64_json="invalid!")]),
        ]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                self.client.images.edit.return_value = response
                self.render()
            self.assertEqual(self.output.read_bytes(), b"existing asset")

    def test_capabilities_and_validation_agree(self):
        self.assertEqual(
            set(ALLOWED_MODELS),
            {
                DEFAULT_MODEL,
                REVISION_MODEL,
                "gpt-image-2.6-flare",
                "gpt-image-2.6-sunburst",
                "gpt-image-2",
                "gpt-image-1.5",
                "gpt-image-1-mini",
                "chatgpt-image-latest",
            },
        )
        for model in MODEL_CONFIG:
            for quality in model["qualities"]:
                for canvas in PRESETS:
                    validate_settings(model["id"], quality, canvas)

    def test_26_models_reach_provider_with_selected_quality_and_standard_size(self):
        for model in ("gpt-image-2.6-flare", "gpt-image-2.6-sunburst"):
            for quality in ("high", "xhigh", "max"):
                with self.subTest(model=model, quality=quality):
                    self.render(
                        model=model, quality=quality, canvas="banner", aplus=True
                    )
                    kwargs = self.client.images.edit.call_args.kwargs
                    self.assertEqual(kwargs["model"], model)
                    self.assertEqual(kwargs["quality"], quality)
                    self.assertEqual(kwargs["size"], "1536x1024")
            with self.assertRaises(ValueError):
                validate_native_size(model, "square", "2048x2048", (model,))

    def test_dated_model_snapshots_are_rejected_before_provider_calls(self):
        for model in (
            "gpt-image-2-2026-04-21",
            "gpt-image-2.5-flare-2026-09-08",
            "gpt-image-2.5-sunburst-2026-09-08",
        ):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.render(model=model)
        self.client.images.edit.assert_not_called()

    def test_legacy_models_reject_extra_quality_and_use_standard_sizes(self):
        for model in (
            "gpt-image-2",
            "gpt-image-1.5",
            "gpt-image-1-mini",
            "chatgpt-image-latest",
        ):
            with self.assertRaises(ValueError):
                self.render(model=model, quality="max")
        self.render(model="gpt-image-1-mini", canvas="banner", aplus=True)
        self.assertEqual(self.client.images.edit.call_args.kwargs["size"], "1536x1024")
        self.render(model=DEFAULT_MODEL, canvas="banner", aplus=True)
        self.assertEqual(self.client.images.edit.call_args.kwargs["size"], "1536x640")

    def test_response_callback_precedes_image_decoding_failure(self):
        self.client.images.edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json="invalid!")], usage={"output_tokens": 10}
        )
        events = []
        with self.assertRaises(RuntimeError):
            self.render(
                on_request=lambda: events.append("request"),
                on_response=lambda response: events.append(response.usage),
            )
        self.assertEqual(events, ["request", {"output_tokens": 10}])


if __name__ == "__main__":
    unittest.main()
