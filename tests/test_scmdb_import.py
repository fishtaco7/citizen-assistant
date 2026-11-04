"""Recipe import: one entry per blueprint, keyed by its product name."""
import unittest

from tools.scmdb_import import build_crafting


def _blueprint(name, slots):
    return {
        "productName": name,
        "tag": f"BP_{name.upper().replace(' ', '_')}",
        "productEntityClass": name.lower(),
        "tiers": [{"craftTimeSeconds": 60, "slots": slots}],
    }


def _slot(*options, modifiers=None):
    return {"name": "Frame", "options": list(options),
            "modifiers": modifiers or []}


def _option(name, quantity=1.0, min_quality=0):
    return {"type": "resource", "quantity": quantity,
            "minQuality": min_quality, "resourceName": name}


class BuildCraftingTests(unittest.TestCase):
    def test_entry_is_keyed_by_product_not_material(self):
        # Regression: the option loop used to reuse the product's variable, so
        # every recipe ended up under its last material's name.
        recipes = {"blueprints": [
            _blueprint("Test Widget", [_slot(_option("Iron"),
                                             _option("Titanium"))]),
        ]}
        crafting, unmatched, unreleased = build_crafting(recipes, {})
        self.assertEqual(list(crafting), ["Test Widget"])
        self.assertEqual(unmatched, 0)
        self.assertEqual(unreleased, 0)

    def test_recipes_no_pool_awards_are_skipped(self):
        recipes = {"blueprints": [
            _blueprint("Test Widget", [_slot(_option("Iron"))]),
            _blueprint("Phantom Widget", [_slot(_option("Iron"))]),
        ]}
        crafting, _, unreleased = build_crafting(
            recipes, {}, {"test widget"})
        self.assertEqual(list(crafting), ["Test Widget"])
        self.assertEqual(unreleased, 1)

    def test_every_option_is_kept_when_there_is_a_choice(self):
        recipes = {"blueprints": [
            _blueprint("Test Widget", [_slot(_option("Iron", 1.0, 0),
                                             _option("Titanium", 2.0, 400))]),
        ]}
        material = (build_crafting(recipes, {})[0]["Test Widget"]
                    ["tiers"][0]["materials"][0])
        self.assertEqual([option["name"] for option in material["options"]],
                         ["Iron", "Titanium"])
        self.assertEqual(material["name"], "Iron")
        self.assertEqual(material["quantity"], 1.0)
        self.assertEqual(material["options"][1]["minQuality"], 400)

    def test_single_option_is_not_duplicated(self):
        recipes = {"blueprints": [
            _blueprint("Test Widget", [_slot(_option("Iron"))]),
        ]}
        material = (build_crafting(recipes, {})[0]["Test Widget"]
                    ["tiers"][0]["materials"][0])
        self.assertNotIn("options", material)

    def test_falls_back_to_the_entity_name(self):
        blueprint = _blueprint("", [_slot(_option("Iron"))])
        blueprint["productName"] = None
        blueprint["productEntityClass"] = "guid-123"
        crafting = build_crafting({"blueprints": [blueprint]},
                                  {"guid-123": "Named Elsewhere"})[0]
        self.assertEqual(list(crafting), ["Named Elsewhere"])


if __name__ == "__main__":
    unittest.main()
